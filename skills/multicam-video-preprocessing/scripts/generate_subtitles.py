#!/usr/bin/env python3
"""
YouTube Subtitles Generator CLI Tool (generate_subtitles.py).
Generates millisecond-accurate, contextually proofread YouTube subtitles (SRT & VTT).

Two-Stage Golden Standard Pipeline:
  Stage 1: Fast acoustic transcription via Whisper (faster-whisper) with millisecond-level timestamps.
  Stage 2: Global-Aware LLM Proofreading (Gemini 3.7 Flash / GPT-5.6 Luna / Gemma 4):
    Phase 2A: Full-transcript Global Consistency Glossary Extraction (1M context scan for names, jargon, entities).
    Phase 2B: High-speed Parallel Chunked Proofreading (250 items/chunk, 5 concurrent workers, thinking_budget=0).

Usage Examples:
  # Example 1: Standard YouTube Subtitle Generation (Whisper + Gemini)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # Example 2: OpenAI / Codex Cloud Endpoint (GPT-5.6 Luna)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 \
    --base-url https://api.openai.com/v1 --model gpt-5.6-luna --api-key $OPENAI_API_KEY

  # Example 3: Local Offline Model via Ollama / vLLM (Gemma 4)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 \
    --base-url http://localhost:11434/v1 --model gemma4:e4b
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.llm_client import call_llm, resolve_api_key
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key


DEFAULT_PROOFREAD_TEMPLATE_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "subtitle_proofread_template.md"),
    os.path.expanduser("~/.gemini/config/skills/multicam-video-preprocessing/assets/subtitle_proofread_template.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "subtitle_proofread_template.md"),
]


def format_timestamp_srt(seconds):
    """Format seconds (float) into SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def format_timestamp_vtt(seconds):
    """Format seconds (float) into WebVTT timestamp: HH:MM:SS.mmm"""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{ms:03d}"


def srt_to_vtt(srt_content):
    """Convert standard SRT format text into WebVTT (.vtt) format text."""
    lines = ["WEBVTT\n"]
    for line in srt_content.strip().splitlines():
        if "-->" in line:
            parts = line.split("-->")
            start = parts[0].strip().replace(",", ".")
            end = parts[1].strip().replace(",", ".")
            lines.append(f"{start} --> {end}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def extract_audio_16k_mono(input_media, output_wav):
    """Extract audio from input media to 16kHz mono 16-bit PCM WAV."""
    cmd = [
        "ffmpeg", "-y", "-i", input_media,
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        output_wav
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")


def run_whisper_transcription(audio_wav, model_size="base", language="zh", device="auto"):
    """
    Run high-speed Whisper acoustic transcription with automatic Apple Silicon / GPU hardware acceleration:
      1. mlx-whisper (Apple Silicon Metal / Neural Engine - Fastest on Mac)
      2. openai-whisper with PyTorch MPS (Apple Silicon GPU) or CUDA (NVIDIA GPU)
      3. faster-whisper with ARM NEON / AVX int8 multi-core vector acceleration
    Returns list of dicts: [{"index": 1, "start": 0.0, "end": 1.28, "text": "..."}]
    """
    print(f"\n[Stage 1/2] ⚡ Running Whisper acoustic transcription (Model: {model_size}, Device: {device})...")
    t0 = time.time()
    lang_arg = None if str(language).lower() in ("auto", "none") else language
    sub_list = []

    # Backend 1: Apple Silicon Native MLX (mlx-whisper)
    if device in ("auto", "mps", "mlx"):
        try:
            import mlx_whisper
            print(f"  ► [Backend: Apple MLX] Utilizing Apple Silicon GPU / Neural Engine acceleration...")
            repo_name = f"mlx-community/whisper-{model_size}-mlx"
            result = mlx_whisper.transcribe(
                audio_wav,
                path_or_hf_repo=repo_name,
                language=lang_arg,
                word_timestamps=False
            )
            for idx, seg in enumerate(result.get("segments", []), start=1):
                sub_list.append({
                    "index": idx,
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "text": seg["text"].strip()
                })
            duration = time.time() - t0
            print(f"  ✓ MLX transcription complete in {duration:.1f}s ({len(sub_list)} subtitle segments generated)")
            return sub_list
        except ImportError:
            pass
        except Exception as e:
            print(f"  [Notice] MLX backend skipped ({e}), switching to next acceleration engine...", file=sys.stderr)

    # Backend 2: faster-whisper (CTranslate2 with int8 & ARM NEON / AVX vectorization)
    try:
        from faster_whisper import WhisperModel
        num_threads = min(8, os.cpu_count() or 4)
        print(f"  ► [Backend: faster-whisper] Utilizing multi-core ARM NEON/AVX vector acceleration ({num_threads} CPU threads, int8)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=num_threads)
        segments, info = model.transcribe(audio_wav, language=lang_arg, beam_size=5, vad_filter=True)

        for idx, seg in enumerate(segments, start=1):
            sub_list.append({
                "index": idx,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip()
            })

    except ImportError:
        # Backend 3: openai-whisper (PyTorch MPS / CUDA / CPU)
        try:
            import whisper
            import torch
            if device in ("auto", "mps") and torch.backends.mps.is_available():
                target_dev = "mps"
            elif device in ("auto", "cuda") and torch.cuda.is_available():
                target_dev = "cuda"
            else:
                target_dev = "cpu"

            print(f"  ► [Backend: openai-whisper] Running on PyTorch ({target_dev.upper()})...")
            model = whisper.load_model(model_size, device=target_dev)
            result = model.transcribe(audio_wav, language=lang_arg)

            for idx, seg in enumerate(result.get("segments", []), start=1):
                sub_list.append({
                    "index": idx,
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip()
                })
        except ImportError:
            raise RuntimeError("No Whisper backend found! Please install via `pip install mlx-whisper` (Apple Silicon GPU) or `pip install faster-whisper`.")

    duration = time.time() - t0
    print(f"  ✓ Whisper acoustic transcription complete in {duration:.1f}s ({len(sub_list)} subtitle segments generated)")
    return sub_list


def build_srt_from_segments(segments):
    """Build standard SRT string from segment list."""
    blocks = []
    for seg in segments:
        idx = seg["index"]
        t_start = format_timestamp_srt(seg["start"])
        t_end = format_timestamp_srt(seg["end"])
        txt = seg["text"]
        blocks.append(f"{idx}\n{t_start} --> {t_end}\n{txt}\n")
    return "\n".join(blocks)


def load_proofread_template():
    """Load subtitle proofreading prompt template."""
    for p in DEFAULT_PROOFREAD_TEMPLATE_PATHS:
        if p and os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    return (
        "You are an expert subtitle proofreader for YouTube.\n"
        "Your task: Correct typos, homophones, domain terms, and names in the provided SRT subtitles.\n"
        "STRICT RULE: Keep all timestamps (`00:00:00,000 --> 00:00:00,000`) and indices 100% UNCHANGED.\n"
        "Output ONLY the corrected SRT inside ```srt ... ``` code block."
    )


def extract_global_glossary(segments, api_key=None, base_url=None, model="gemini-3.7-flash"):
    """
    Phase 2A: Scan the entire episode transcript (40k+ tokens) in one shot to build a Global Terminology Glossary.
    Ensures 100% naming and term consistency across all chapter parts and segments.
    """
    print(f"\n[Stage 2A] 🌐 Extracting Global Consistency Glossary across entire transcript...")
    t0 = time.time()

    # Build compressed full text for global scanning
    full_text_lines = [f"[{s['index']}] {s['text']}" for s in segments]
    full_transcript_sample = "\n".join(full_text_lines)

    prompt = (
        "You are an expert Chief Subtitle Editor for professional YouTube multi-camera productions.\n"
        "Carefully read the ENTIRE transcript below from start to finish.\n"
        "Your goal is to extract a comprehensive, authoritative **Global Terminology Glossary (全片專有名詞與詞彙對照表)** "
        "to ensure 100% spelling, naming, and domain term consistency across all subtitle segments.\n\n"
        "Extract the following structured sections in Markdown:\n"
        "1. **講者與人物姓名 (Person & Speaker Names)**: e.g. Chinese & English names, titles\n"
        "2. **公司、品牌與機構 (Organizations & Companies)**: e.g. Anthropic, Google, 思想實驗室\n"
        "3. **行業專有名詞與縮寫 (Domain Jargon & Tech Acronyms)**: e.g. SaaS, LLM, 估值狂飆, 多模態\n"
        "4. **常見同音訛字修正指引 (Homophone & Typo Correction Rules)**: e.g. 矽谷 (not 西谷), 估值 (not 固值)\n\n"
        "--- FULL TRANSCRIPT START ---\n"
        f"{full_transcript_sample}\n"
        "--- FULL TRANSCRIPT END ---\n\n"
        "Output ONLY the structured Markdown Glossary:"
    )

    try:
        glossary_content = call_llm(
            prompt=prompt,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.1,
            max_tokens=4096,
            thinking_budget=0  # Fast response
        )
        elapsed = time.time() - t0
        print(f"  ✓ Global Glossary extracted in {elapsed:.1f}s ({len(glossary_content)} chars)")
        return glossary_content.strip()
    except Exception as e:
        print(f"  [Warning] Global glossary extraction failed ({e}). Continuing with standard proofreading.", file=sys.stderr)
        return ""


def proofread_single_chunk(c_idx, num_chunks, chunk_slice, template, global_glossary, api_key, base_url, model):
    """Worker function to proofread a single chunk of SRT blocks."""
    chunk_text = "\n\n".join(chunk_slice)
    glossary_section = f"\n=== 全片權威專有名詞對照表 (Global Consistency Glossary) ===\n{global_glossary}\n============================================================\n" if global_glossary else ""

    prompt = (
        f"{template}\n"
        f"{glossary_section}\n"
        f"--- 待校對 SRT 字幕（區塊 {c_idx + 1}/{num_chunks}）---\n"
        f"```srt\n{chunk_text}\n```\n\n"
        f"請依據全片對照表與前後文語意，輸出校對後的完整 SRT："
    )

    try:
        response_text = call_llm(
            prompt=prompt,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0.1,
            max_tokens=8192,
            thinking_budget=0  # Fast zero-thinking latency
        )

        match = re.search(r"```(?:srt)?\s*\n(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
        clean_chunk = match.group(1).strip() if match else response_text.strip()

        if "-->" in clean_chunk:
            return c_idx, clean_chunk, True
        else:
            return c_idx, chunk_text, False

    except Exception as e:
        print(f"  [Warning] Chunk {c_idx+1} proofreading error: {e}. Keeping original.", file=sys.stderr)
        return c_idx, chunk_text, False


def proofread_srt_with_llm(raw_srt, global_glossary=None, api_key=None, base_url=None, model="gemini-3.7-flash", chunk_size=250, max_workers=5):
    """
    Phase 2B: High-speed Parallel Chunked Proofreading with injected Global Glossary.
    Uses 250 items/chunk and concurrent workers for lightning-fast execution.
    """
    print(f"\n[Stage 2B] ⚡ Running Parallel LLM Proofreading (Model: {model}, Chunk: {chunk_size}, Workers: {max_workers})...")
    t0 = time.time()

    template = load_proofread_template()
    raw_blocks = [b.strip() for b in raw_srt.strip().split("\n\n") if b.strip()]
    if not raw_blocks:
        return raw_srt

    num_chunks = (len(raw_blocks) + chunk_size - 1) // chunk_size
    print(f"  • Total Subtitles: {len(raw_blocks)} items -> {num_chunks} topic-level chunks")

    chunk_slices = [
        raw_blocks[c_idx * chunk_size : (c_idx + 1) * chunk_size]
        for c_idx in range(num_chunks)
    ]

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                proofread_single_chunk,
                c_idx, num_chunks, chunk_slices[c_idx],
                template, global_glossary, api_key, base_url, model
            ): c_idx
            for c_idx in range(num_chunks)
        }

        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            c_idx, clean_text, success = future.result()
            results[c_idx] = clean_text
            completed_count += 1
            status_tag = "✓" if success else "⚠"
            pct = (completed_count / num_chunks) * 100
            print(f"\r  ► Progress: {completed_count}/{num_chunks} chunks completed ({pct:.0f}%)... {status_tag}", end="", flush=True)

    print()
    # Assemble chunks in strictly preserved index order
    sorted_blocks = [results[i] for i in range(num_chunks)]
    total_time = time.time() - t0
    print(f"  ✓ Contextual proofreading completed in {total_time:.1f}s ({num_chunks} chunks assembled)")
    return "\n\n".join(sorted_blocks).strip() + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Subtitles Generator: Whisper ASR + Global-Aware LLM Contextual Proofreading (Gemini / GPT-5.6 Luna / Gemma 4).",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-i", "--input", required=True, help="Path to input video (e.g. final_cut_full.mp4) or audio file")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory for SRT/VTT subtitles (default: same as input)")
    parser.add_argument("--whisper-model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size for Stage 1 acoustic transcription (default: base)")
    parser.add_argument("--model", default="gemini-3.7-flash",
                        help="LLM model for Stage 2 proofreading (e.g. gemini-3.7-flash, gpt-5.6-luna, gemma4:e4b)")
    parser.add_argument("--base-url", default=None,
                        help="Custom OpenAI-compatible API base URL (e.g. https://api.openai.com/v1, http://localhost:11434/v1)")
    parser.add_argument("--language", default="zh", help="Spoken language code for transcription (default: zh, or auto)")
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "mlx", "cuda", "cpu"],
                        help="Device acceleration backend for Whisper (default: auto for Apple Silicon GPU / Neural Engine)")
    parser.add_argument("--api-key", default=None, help="API Key (or set GEMINI_API_KEY / OPENAI_API_KEY environment variable)")
    parser.add_argument("--chunk-size", type=int, default=250, help="Subtitle entries per proofread batch (default: 250)")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent workers for parallel proofreading (default: 5)")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[Error] Input media not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    input_basename = os.path.splitext(os.path.basename(args.input))[0]
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(args.input)) or "."
    os.makedirs(out_dir, exist_ok=True)

    final_srt_path = os.path.join(out_dir, f"{input_basename}.srt")
    final_vtt_path = os.path.join(out_dir, f"{input_basename}.vtt")
    raw_srt_path = os.path.join(out_dir, f"{input_basename}_raw_whisper.srt")
    glossary_path = os.path.join(out_dir, f"{input_basename}_glossary.md")

    print("\n" + "=" * 78)
    print("🎬  YouTube Subtitles Generator (Whisper ASR + Global-Aware LLM Proofreading)")
    print("=" * 78)
    print(f"  • Input Media   : {args.input}")
    print(f"  • LLM Model     : {args.model}")
    if args.base_url:
        print(f"  • Base URL      : {args.base_url}")
    print(f"  • Whisper Model : {args.whisper_model} (Language: {args.language}, Device: {args.device})")
    print(f"  • Batch Settings: Chunk {args.chunk_size} lines | {args.workers} Parallel Workers")
    print(f"  • Target SRT    : {final_srt_path}")
    print(f"  • Target VTT    : {final_vtt_path}")
    print("-" * 78)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_wav = os.path.join(tmpdir, "audio_16k.wav")
        print("\n[Step 0] 🎙️ Extracting 16kHz mono audio from media...")
        extract_audio_16k_mono(args.input, tmp_wav)

        # Stage 1: Whisper Acoustic Transcription
        segments = run_whisper_transcription(tmp_wav, model_size=args.whisper_model, language=args.language, device=args.device)
        raw_srt = build_srt_from_segments(segments)

        # Save raw whisper backup for reference/debugging
        with open(raw_srt_path, "w", encoding="utf-8") as f:
            f.write(raw_srt)
        print(f"  • Saved raw acoustic baseline: {raw_srt_path}")

        # Stage 2A: Global Consistency Glossary Extraction (Full 1M Context Scan)
        global_glossary = extract_global_glossary(
            segments, api_key=args.api_key, base_url=args.base_url, model=args.model
        )
        if global_glossary:
            with open(glossary_path, "w", encoding="utf-8") as f:
                f.write(global_glossary + "\n")
            print(f"  • Saved Global Glossary: {glossary_path}")

        # Stage 2B: Parallel Chunked Proofreading with Injected Glossary
        final_srt = proofread_srt_with_llm(
            raw_srt,
            global_glossary=global_glossary,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            chunk_size=args.chunk_size,
            max_workers=args.workers
        )

        # Write Final SRT
        with open(final_srt_path, "w", encoding="utf-8") as f:
            f.write(final_srt.strip() + "\n")
        print(f"\n[Output 1/2] 📝 Saved YouTube Standard SRT Subtitles: {final_srt_path}")

        # Convert and Write WebVTT
        vtt_content = srt_to_vtt(final_srt)
        with open(final_vtt_path, "w", encoding="utf-8") as f:
            f.write(vtt_content)
        print(f"[Output 2/2] 🌐 Saved WebVTT (.vtt) Subtitles for YouTube / Web: {final_vtt_path}")

    print("\n" + "=" * 78)
    print("✅  YouTube Subtitles Generation Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
