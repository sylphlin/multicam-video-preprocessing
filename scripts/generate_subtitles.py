#!/usr/bin/env python3
"""
YouTube Subtitles Generator CLI Tool (generate_subtitles.py).
Generates millisecond-accurate, contextually proofread YouTube subtitles (SRT & VTT).

Two-Stage Golden Standard Pipeline (Mandatory):
  Stage 1: Fast acoustic transcription via Whisper (faster-whisper) with millisecond-level timestamps.
  Stage 2: Contextual LLM Proofreading (Gemini 3.7 Flash) to eliminate homophones, typos, and domain term mistakes without touching timestamps.

Usage Examples:
  # Example 1: Standard End-to-End YouTube Subtitle Generation (Whisper + Gemini)
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # Example 2: Offline Mode with specific Whisper model
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --mode whisper-only --whisper-model small

  # Example 3: Custom Output Directory and Language
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 -o ./subtitles/ --language zh
"""

import argparse
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
    # Replace comma in timestamps with dot
    # e.g., 00:01:23,450 --> 00:01:26,800  =>  00:01:23.450 --> 00:01:26.800
    for line in srt_content.strip().splitlines():
        if "-->" in line:
            parts = line.split("-->")
            start = parts[0].strip().replace(",", ".")
            end = parts[1].strip().replace(",", ".")
            lines.append(f"{start} --> {end}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def get_api_key(cli_key=None):
    """Retrieve Gemini API key from CLI argument or environment variables."""
    if cli_key:
        return cli_key
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var)
        if val:
            return val
    return None


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


def run_whisper_transcription(audio_wav, model_size="base", language="zh"):
    """
    Run local Whisper transcription using faster-whisper (or fallback to openai-whisper).
    Returns list of dicts: [{"index": 1, "start": 0.0, "end": 1.28, "text": "..."}]
    """
    print(f"\n[Stage 1/2] ⚡ Running Whisper acoustic transcription (Model: {model_size})...")
    t0 = time.time()

    try:
        from faster_whisper import WhisperModel
        # Initialize model (cpu int8 for instant low-memory Mac/PC performance)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        lang_arg = None if language.lower() in ("auto", "none") else language
        segments, info = model.transcribe(audio_wav, language=lang_arg, beam_size=5)

        sub_list = []
        for idx, seg in enumerate(segments, start=1):
            sub_list.append({
                "index": idx,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip()
            })

    except ImportError:
        try:
            import whisper
            model = whisper.load_model(model_size)
            lang_arg = None if language.lower() in ("auto", "none") else language
            result = model.transcribe(audio_wav, language=lang_arg)

            sub_list = []
            for idx, seg in enumerate(result.get("segments", []), start=1):
                sub_list.append({
                    "index": idx,
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip()
                })
        except ImportError:
            raise RuntimeError("Neither 'faster_whisper' nor 'whisper' is installed! Please install via `pip install faster-whisper`.")

    duration = time.time() - t0
    print(f"  ✓ Whisper transcription complete in {duration:.1f}s ({len(sub_list)} subtitle segments generated)")
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


def proofread_srt_with_gemini(raw_srt, api_key, model="gemini-3.7-flash", chunk_size=60):
    """
    Call Gemini API in batches to proofread the SRT subtitles line-by-line while preserving timestamps.
    """
    print(f"\n[Stage 2/2] 🤖 Running Gemini LLM contextual proofreading (Model: {model})...")
    t0 = time.time()

    template = load_proofread_template()

    # Split SRT into blocks
    raw_blocks = [b.strip() for b in raw_srt.strip().split("\n\n") if b.strip()]
    if not raw_blocks:
        return raw_srt

    print(f"  • Total subtitle entries: {len(raw_blocks)} (Batch chunk size: {chunk_size})")

    proofread_blocks = []
    num_chunks = (len(raw_blocks) + chunk_size - 1) // chunk_size

    for c_idx in range(num_chunks):
        chunk_slice = raw_blocks[c_idx * chunk_size : (c_idx + 1) * chunk_size]
        chunk_text = "\n\n".join(chunk_slice)

        prompt = (
            f"{template}\n\n"
            f"--- 待校對 SRT 字幕（區塊 {c_idx + 1}/{num_chunks}）---\n"
            f"```srt\n{chunk_text}\n```\n\n"
            f"請輸出校對後的完整 SRT："
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                text_parts = resp_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                response_text = "".join([p.get("text", "") for p in text_parts])
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            print(f"  [Warning] Gemini proofreading failed on chunk {c_idx+1} (HTTP {e.code}): {err}. Keeping original chunk.", file=sys.stderr)
            proofread_blocks.extend(chunk_slice)
            continue

        # Extract SRT from markdown code block
        match = re.search(r"```(?:srt)?\s*\n(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
        if match:
            clean_chunk = match.group(1).strip()
        else:
            clean_chunk = response_text.strip()

        # Sanity check: Ensure cleaned chunk has timestamps
        if "-->" in clean_chunk:
            proofread_blocks.append(clean_chunk)
            print(f"  ✓ Proofread chunk {c_idx + 1}/{num_chunks} ({len(chunk_slice)} items)")
        else:
            print(f"  [Warning] Missing timestamps in Gemini response for chunk {c_idx + 1}. Keeping original chunk.")
            proofread_blocks.extend(chunk_slice)

    total_time = time.time() - t0
    print(f"  ✓ Contextual proofreading completed in {total_time:.1f}s")
    return "\n\n".join(proofread_blocks).strip() + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Subtitles Generator: Millisecond-accurate ASR (Whisper) + Contextual LLM Proofreading (Gemini).",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-i", "--input", required=True, help="Path to input video (e.g. final_cut_full.mp4) or audio file")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory for SRT/VTT subtitles (default: same as input)")
    parser.add_argument("--whisper-model", default="base", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model size for Stage 1 acoustic transcription (default: base)")
    parser.add_argument("--gemini-model", default="gemini-3.7-flash",
                        help="Gemini model for Stage 2 proofreading (default: gemini-3.7-flash)")
    parser.add_argument("--language", default="zh", help="Spoken language code for transcription (default: zh, or auto)")
    parser.add_argument("--api-key", default=None, help="Gemini API Key (or set GEMINI_API_KEY / GOOGLE_API_KEY environment variable)")
    parser.add_argument("--chunk-size", type=int, default=50, help="Subtitle entries per proofread batch (default: 50)")

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

    api_key = get_api_key(args.api_key)
    if not api_key:
        print("[Error] Missing Gemini API Key for subtitle proofreading! Please pass --api-key or set GEMINI_API_KEY / GOOGLE_API_KEY environment variable.", file=sys.stderr)
        print("  • To ensure broadcast-grade quality and avoid homophone typos, Whisper + Gemini proofreading is mandatory.", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 78)
    print("🎬  YouTube Subtitles Generator (Whisper + Gemini Proofreading)")
    print("=" * 78)
    print(f"  • Input Media   : {args.input}")
    print(f"  • Pipeline Mode : Whisper ASR + Gemini 3.7 Flash Proofreading (Sole Standard)")
    print(f"  • Whisper Model : {args.whisper_model} (Language: {args.language})")
    print(f"  • Target SRT    : {final_srt_path}")
    print(f"  • Target VTT    : {final_vtt_path}")
    print("-" * 78)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_wav = os.path.join(tmpdir, "audio_16k.wav")
        print("\n[Step 0] 🎙️ Extracting 16kHz mono audio from media...")
        extract_audio_16k_mono(args.input, tmp_wav)

        # Stage 1: Whisper Acoustic Transcription
        segments = run_whisper_transcription(tmp_wav, model_size=args.whisper_model, language=args.language)
        raw_srt = build_srt_from_segments(segments)

        # Save raw whisper backup for reference/debugging
        with open(raw_srt_path, "w", encoding="utf-8") as f:
            f.write(raw_srt)
        print(f"  • Saved raw acoustic baseline: {raw_srt_path}")

        # Stage 2: Mandatory Gemini Contextual Proofreading
        final_srt = proofread_srt_with_gemini(raw_srt, api_key, model=args.gemini_model, chunk_size=args.chunk_size)

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
