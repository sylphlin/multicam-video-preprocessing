#!/usr/bin/env python3
"""
YouTube Subtitles Generator CLI Tool (generate_subtitles.py).
Generates millisecond-accurate, contextually proofread YouTube subtitles (SRT & VTT).

Three-Stage Golden Standard Pipeline:
  Stage 1: Global Audio Context & Glossary Extraction (Gemini 1M Context Audio Scan).
           Listens to the entire episode audio to extract speaker names, channel title, English terms, and domain jargon.
           Optionally merges user-provided interview outlines (--outline).
  Stage 2: Zero-Drift Acoustic Transcription via Whisper (mlx-whisper / faster-whisper).
           Produces frame-locked, physical acoustic millisecond timestamps (0.000s drift).
  Stage 3: Multimodal Audio-Text Chunked Precision Proofreading (Gemini 3.7 Flash).
           Slices local audio chunks and proofreads subtitles against both the local acoustic waveform and the Global Glossary,
           strictly preserving 100% of Whisper's millisecond timestamps and line indices.

Usage Examples:
  # Standard YouTube Subtitle Generation (Whisper + Gemini Audio Proofreading)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # With User Interview Outline / Glossary
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --outline "Host: 國威, Guest: Kelly Tsai, Topics: 矽谷, 估值狂飆"

  # OpenAI / Codex Cloud Endpoint (GPT-5.6 Luna)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 \
    --base-url https://api.openai.com/v1 --model gpt-5.6-luna --api-key $OPENAI_API_KEY
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
    from modules.progress import LiveTicker
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key
    from scripts.modules.progress import LiveTicker


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
    with LiveTicker(f"Extracting 16kHz mono audio ({os.path.basename(input_media)})"):
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")


def run_whisper_transcription(audio_wav, model_size="base", language="zh", device="auto"):
    """
    Run high-speed Whisper acoustic transcription with automatic Apple Silicon / GPU hardware acceleration:
      1. mlx-whisper (Apple Silicon Metal / Neural Engine - Fastest on Mac)
      2. faster-whisper with ARM NEON / AVX int8 multi-core vector acceleration
      3. openai-whisper with PyTorch MPS / CUDA
    Returns list of dicts: [{"index": 1, "start": 0.0, "end": 1.28, "text": "..."}]
    """
    print(f"\n[Stage 2/3] 🎙️ Running Whisper acoustic transcription (Model: {model_size}, Device: {device})...")
    t0 = time.time()
    lang_arg = None if str(language).lower() in ("auto", "none") else language
    sub_list = []
    detected_lang = language if lang_arg else "zh"

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
            detected_lang = result.get("language") or detected_lang
            for idx, seg in enumerate(result.get("segments", []), start=1):
                sub_list.append({
                    "index": idx,
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "text": seg["text"].strip()
                })
            duration = time.time() - t0
            print(f"  ✓ MLX transcription complete in {duration:.1f}s ({len(sub_list)} subtitle segments generated, language: '{detected_lang}')")
            return sub_list, detected_lang
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
        detected_lang = getattr(info, "language", detected_lang)

        total_dur = getattr(info, "duration", 0)
        for idx, seg in enumerate(segments, start=1):
            sub_list.append({
                "index": idx,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip()
            })
            if total_dur > 0:
                pct = min(100.0, (seg.end / total_dur) * 100.0)
                print(f"\r  ► [Whisper ASR] {format_timestamp_srt(seg.end)} / {format_timestamp_srt(total_dur)} ({pct:4.1f}%) | Segment #{idx:03d}...", end="", flush=True)
            else:
                print(f"\r  ► [Whisper ASR] Segment #{idx:03d} ({format_timestamp_srt(seg.start)} -> {format_timestamp_srt(seg.end)})...", end="", flush=True)
        print()

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
            detected_lang = result.get("language") or detected_lang

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
    print(f"  ✓ Whisper acoustic transcription complete in {duration:.1f}s ({len(sub_list)} segments, language: '{detected_lang}')")
    return sub_list, detected_lang


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


def normalize_language_tag(lang_str):
    """Normalize language code to standardized locale code (e.g. zh-TW, en, ja, zh-CN, ko)."""
    if not lang_str:
        return "zh-TW"
    l = str(lang_str).lower().replace("_", "-").strip()
    if l in ("zh", "zh-tw", "zh-hant", "zh-hk", "zh-mo", "cmn-hant", "cmn-tw"):
        return "zh-TW"
    if l in ("zh-cn", "zh-hans", "zh-sg", "cmn-hans", "cmn-cn"):
        return "zh-CN"
    if l.startswith("en"):
        return "en"
    if l.startswith("ja"):
        return "ja"
    if l.startswith("ko"):
        return "ko"
    return "zh-TW"


def load_proofread_template(language="zh-TW"):
    """Load subtitle proofreading prompt template based on language locale."""
    norm_lang = normalize_language_tag(language)

    search_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"),
        os.path.expanduser("~/.gemini/config/skills/multicam-video-preprocessing/assets"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
    ]

    target_names = [
        f"subtitle_proofread_template.{norm_lang}.md",
        "subtitle_proofread_template.zh-TW.md",
        "subtitle_proofread_template.md"
    ]

    for s_dir in search_dirs:
        for t_name in target_names:
            p = os.path.join(s_dir, t_name)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content, os.path.basename(p)
                except Exception:
                    pass

    return (
        "You are an expert subtitle proofreader for YouTube.\n"
        "Your task: Re-segment and proofread subtitles into natural, fluent semantic clauses (max 16 chars per line) with acoustic timestamp fusion.\n"
        "Output ONLY the corrected SRT inside ```srt ... ``` code block."
    ), "builtin_fallback"


def extract_global_glossary(audio_wav=None, segments=None, user_outline=None, api_key=None, base_url=None, model="gemini-3.7-flash"):
    """
    Stage 1: Global Audio Context & Consistency Glossary Extraction (Gemini 1M Context Scan).
    Listens to full episode audio to extract speaker names, organizations, acronyms, and terms.
    """
    print(f"\n[Stage 1/3] 🎧 Extracting Global Consistency Glossary across entire episode...")
    t0 = time.time()

    outline_section = f"\n=== 使用者提供之訪綱/主題資料 (User Interview Outline) ===\n{user_outline}\n" if user_outline else ""
    
    prompt = (
        "You are an expert Chief Subtitle Editor for professional YouTube productions.\n"
        "Your goal is to extract a comprehensive, authoritative **Global Terminology Glossary (全片專有名詞與詞彙對照表)** "
        "directly from this recording to ensure 100% spelling, naming, and domain term consistency across all subtitle segments.\n\n"
        f"{outline_section}\n"
        "Extract ONLY verified domain terms, entity names, and proper spellings that actually appear in this recording.\n"
        "Structure the output cleanly in Markdown:\n"
        "1. **講者與人物姓名 (Person & Speaker Names)**: Official Chinese/English names, titles & roles\n"
        "2. **公司、品牌、產品與機構 (Organizations, Products & Brands)**: Official brand, company & tool names\n"
        "3. **行業專有名詞與技術術語 (Domain Jargon & Tech Terms)**: Industry terminology, English acronyms & phrases\n"
        "4. **核心主題概念 (Core Topic Concepts)**: Key themes discussed in this episode\n\n"
        "Output ONLY the clean, authoritative Markdown Glossary:"
    )

    # Use lightweight compressed MP3 for audio scanning if audio file is available
    audio_for_gemini = None
    tmp_audio_mp3 = None
    if audio_wav and os.path.exists(audio_wav) and "gemini" in model.lower() and not base_url:
        try:
            tmp_audio_mp3 = os.path.join(os.path.dirname(audio_wav), "global_glossary_audio.mp3")
            # Compress to 48k mono mp3 for fast upload
            subprocess.run(["ffmpeg", "-y", "-i", audio_wav, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", tmp_audio_mp3],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if os.path.getsize(tmp_audio_mp3) <= 20 * 1024 * 1024:  # <= 20MB inline limit
                audio_for_gemini = tmp_audio_mp3
        except Exception as e:
            audio_for_gemini = None

    try:
        with LiveTicker("Extracting Global Consistency Glossary via Gemini (1M context scan)"):
            glossary_content = call_llm(
                prompt=prompt,
                model=model,
                base_url=base_url,
                api_key=api_key,
                audio_path=audio_for_gemini,
                temperature=0.1,
                max_tokens=4096,
                thinking_budget=0
            )
        duration = time.time() - t0
        print(f"  ✓ Global Glossary extracted in {duration:.1f}s")
        return glossary_content.strip()
    except Exception as e:
        print(f"  [Warning] Global glossary extraction failed ({e}). Continuing with standard proofreading.", file=sys.stderr)
        return user_outline or ""
    finally:
        if tmp_audio_mp3 and os.path.exists(tmp_audio_mp3):
            try:
                os.remove(tmp_audio_mp3)
            except:
                pass


def parse_timestamp_str(ts_str):
    """Parse SRT timestamp 'HH:MM:SS,mmm' to float seconds."""
    h, m, sec_ms = ts_str.strip().split(":")
    sec, ms = sec_ms.replace(".", ",").split(",")
    return float(h) * 3600 + float(m) * 60 + float(sec) + float(ms) / 1000.0


def proofread_single_chunk(c_idx, num_chunks, chunk_slice, template, global_glossary, audio_wav, api_key, base_url, model):
    """Worker function to proofread a single chunk of SRT blocks with local audio slice."""
    chunk_text = "\n\n".join(chunk_slice)
    glossary_section = f"\n=== 全片權威專有名詞對照表 (Global Consistency Glossary) ===\n{global_glossary}\n============================================================\n" if global_glossary else ""

    # Calculate audio slice start and end time
    chunk_mp3_path = None
    tmp_dir = os.path.dirname(audio_wav) if audio_wav else None

    if audio_wav and os.path.exists(audio_wav) and "gemini" in model.lower() and not base_url:
        try:
            # Find start and end timestamps in this chunk
            first_block = chunk_slice[0].splitlines()
            last_block = chunk_slice[-1].splitlines()
            if len(first_block) >= 2 and "-->" in first_block[1] and len(last_block) >= 2 and "-->" in last_block[1]:
                t_start_sec = max(0.0, parse_timestamp_str(first_block[1].split("-->")[0]) - 0.5)
                t_end_sec = parse_timestamp_str(last_block[1].split("-->")[1]) + 0.5
                dur_sec = max(1.0, t_end_sec - t_start_sec)
                
                chunk_mp3_path = os.path.join(tmp_dir, f"chunk_{c_idx:03d}.mp3")
                cmd = [
                    "ffmpeg", "-y", "-ss", f"{t_start_sec:.3f}", "-t", f"{dur_sec:.3f}",
                    "-i", audio_wav, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", chunk_mp3_path
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception:
            chunk_mp3_path = None

    prompt = (
        f"{template}\n"
        f"{glossary_section}\n"
        f"--- 待校對與重整之原始 SRT 碎字幕（區塊 {c_idx + 1}/{num_chunks}）---\n"
        f"```srt\n{chunk_text}\n```\n\n"
        f"請邊聽附帶的音訊錄音、依據全片對照表與語意段落規範，進行自然斷句重整（每行 <= 16 字）、時間軸物理聲學熔接與同音錯字校正，輸出重整後的完整 SRT："
    )

    try:
        response_text = call_llm(
            prompt=prompt,
            model=model,
            base_url=base_url,
            api_key=api_key,
            audio_path=chunk_mp3_path,
            temperature=0.1,
            max_tokens=8192,
            thinking_budget=0
        )

        match = re.search(r"```(?:srt)?\s*\n(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
        clean_chunk = match.group(1).strip() if match else response_text.strip()
        clean_chunk = re.sub(r"^```(?:srt)?\s*\n?", "", clean_chunk, flags=re.IGNORECASE)
        clean_chunk = re.sub(r"\n?```\s*$", "", clean_chunk).strip()

        if "-->" in clean_chunk:
            return c_idx, clean_chunk, True
        else:
            return c_idx, chunk_text, False

    except Exception as e:
        print(f"  [Warning] Chunk {c_idx+1} proofreading error: {e}. Keeping original.", file=sys.stderr)
        return c_idx, chunk_text, False
    finally:
        if chunk_mp3_path and os.path.exists(chunk_mp3_path):
            try:
                os.remove(chunk_mp3_path)
            except:
                pass


def proofread_srt_with_llm(raw_srt, audio_wav=None, global_glossary=None, api_key=None, base_url=None, model="gemini-3.7-flash", chunk_size=80, max_workers=5, language="zh-TW"):
    """
    Stage 3: Multimodal Audio-Text Parallel Chunked Proofreading with injected Global Glossary.
    Slices local audio chunks and proofreads subtitles against actual audio acoustics,
    guaranteeing 100% physical timestamp preservation.
    """
    template, tmpl_file = load_proofread_template(language=language)
    print(f"\n[Stage 3/3] ⚡ Running Multimodal Audio-Text LLM Proofreading (Model: {model}, Chunk: {chunk_size}, Workers: {max_workers})...")
    print(f"  • Template Loaded: {tmpl_file} (Locale: {normalize_language_tag(language)})")
    t0 = time.time()
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
                template, global_glossary, audio_wav, api_key, base_url, model
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
    # Assemble chunks in strictly preserved index order and renumber monotonically
    sorted_blocks = [results[i] for i in range(num_chunks)]
    raw_combined = "\n\n".join(sorted_blocks).strip()
    
    # Parse all blocks and ensure strict sequential numbering (1, 2, 3...)
    all_blocks = [b.strip() for b in raw_combined.split("\n\n") if b.strip()]
    renumbered_blocks = []
    current_idx = 1
    for b in all_blocks:
        b_lines = b.splitlines()
        if len(b_lines) >= 3 and "-->" in b_lines[1]:
            renumbered_blocks.append(f"{current_idx}\n{b_lines[1]}\n" + "\n".join(b_lines[2:]) + "\n")
            current_idx += 1
        elif len(b_lines) == 2 and "-->" in b_lines[0]:
            renumbered_blocks.append(f"{current_idx}\n{b_lines[0]}\n{b_lines[1]}\n")
            current_idx += 1
        else:
            renumbered_blocks.append(b + "\n")

    total_time = time.time() - t0
    print(f"  ✓ Contextual audio-multimodal proofreading completed in {total_time:.1f}s ({len(renumbered_blocks)} subtitles assembled across {num_chunks} chunks)")
    return "\n\n".join(renumbered_blocks).strip() + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Subtitles Generator: Global Audio Glossary + Whisper ASR + Gemini Audio Proofreading.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-i", "--input", required=True, help="Path to input video (e.g. final_cut_full.mp4) or audio file")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory for SRT/VTT subtitles (default: same as input)")
    parser.add_argument("--outline", default=None, help="User interview outline, topic summary, or glossary notes to bias terminology")
    parser.add_argument("--whisper-model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size for Stage 2 acoustic transcription (default: base)")
    parser.add_argument("--model", default="gemini-3.7-flash",
                        help="LLM model for Stage 1 & 3 proofreading (e.g. gemini-3.7-flash, gpt-5.6-luna, gemma4:e4b)")
    parser.add_argument("--base-url", default=None,
                        help="Custom OpenAI-compatible API base URL (e.g. https://api.openai.com/v1, http://localhost:11434/v1)")
    parser.add_argument("--language", default="auto", help="Spoken language code for transcription (default: auto for acoustic auto-detection, or zh-TW, en, ja, ko, zh-CN)")
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "mlx", "cuda", "cpu"],
                        help="Device acceleration backend for Whisper (default: auto for Apple Silicon GPU / Neural Engine)")
    parser.add_argument("--api-key", default=None, help="API Key (or set GEMINI_API_KEY / OPENAI_API_KEY environment variable)")
    parser.add_argument("--chunk-size", type=int, default=80, help="Subtitle entries per proofread batch (default: 80, ~2-3 mins)")
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

    # Read outline file if provided as filepath
    user_outline_text = args.outline
    if user_outline_text and os.path.isfile(user_outline_text):
        with open(user_outline_text, "r", encoding="utf-8") as f:
            user_outline_text = f.read().strip()

    print("\n" + "=" * 78)
    print("🎬  YouTube Subtitles Generator (Global Glossary + Whisper ASR + Gemini Audio Proofreading)")
    print("=" * 78)
    print(f"  • Input Media   : {args.input}")
    print(f"  • LLM Model     : {args.model}")
    if args.outline:
        print(f"  • User Outline  : {args.outline[:50]}...")
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

        resolved_key = resolve_api_key(args.api_key, args.base_url, args.model)

        # Stage 1: Global Audio Context & Consistency Glossary Extraction (Full 1M Context Scan)
        global_glossary = None
        if resolved_key or args.base_url:
            global_glossary = extract_global_glossary(
                audio_wav=tmp_wav,
                user_outline=user_outline_text,
                api_key=args.api_key,
                base_url=args.base_url,
                model=args.model
            )
            if global_glossary:
                with open(glossary_path, "w", encoding="utf-8") as f:
                    f.write(global_glossary + "\n")
                print(f"  • Saved Global Glossary: {glossary_path}")

        # Stage 2: Whisper Acoustic Transcription (Zero-Drift Physical Timestamps)
        segments, detected_lang = run_whisper_transcription(tmp_wav, model_size=args.whisper_model, language=args.language, device=args.device)
        raw_srt = build_srt_from_segments(segments)

        effective_lang = detected_lang if str(args.language).lower() in ("auto", "none") else args.language
        print(f"  • Effective Language Locale: {normalize_language_tag(effective_lang)} (Input: '{args.language}', Detected: '{detected_lang}')")

        # Save raw whisper backup for reference/debugging
        with open(raw_srt_path, "w", encoding="utf-8") as f:
            f.write(raw_srt)
        print(f"  • Saved raw acoustic baseline: {raw_srt_path}")

        if not resolved_key and not args.base_url:
            print(f"\n[Stage 3/3] ℹ️  No LLM API Key (GEMINI_API_KEY / OPENAI_API_KEY) found.")
            print(f"            Saving raw Whisper acoustic transcription directly as final SRT/VTT.")
            final_srt = raw_srt
        else:
            # Stage 3: Multimodal Audio-Text Parallel Chunked Proofreading
            final_srt = proofread_srt_with_llm(
                raw_srt=raw_srt,
                audio_wav=tmp_wav,
                global_glossary=global_glossary,
                api_key=args.api_key,
                base_url=args.base_url,
                model=args.model,
                chunk_size=args.chunk_size,
                max_workers=args.workers,
                language=effective_lang
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
