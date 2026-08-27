#!/usr/bin/env python3
"""
AI Multimodal Video to EDL Decision Generator (generate_edl.py).
Uploads multi-camera / multi-in-one merged video to multimodal LLM File API,
applies the editable EDL Prompt Template, and extracts standard EDL CSV + Trimming Report.

Default Model: gemini-3.7-flash (customizable via --model)
Prompt Assets: assets/edl_interview_template.md
"""

import argparse
import csv
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.llm_client import call_llm, resolve_api_key, get_ssl_context
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key, get_ssl_context


DEFAULT_PROMPT_TEMPLATE_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "edl_interview_template.md"),
    os.path.expanduser("~/.gemini/config/plugins/multicam-video-preprocessing/skills/multicam-video-preprocessing/assets/edl_interview_template.md"),
    os.path.expanduser("~/.codex/plugins/multicam-video-preprocessing/skills/multicam-video-preprocessing/assets/edl_interview_template.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "edl_interview_template.md"),
]


def load_prompt_template(custom_path=None):
    """Load prompt markdown template from custom path or standard asset paths."""
    paths_to_try = [custom_path] if custom_path else []
    paths_to_try.extend(DEFAULT_PROMPT_TEMPLATE_PATHS)

    for p in paths_to_try:
        if p and os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
                print(f"  • Loaded Prompt Template: {p}")
                return content

    raise FileNotFoundError(f"Could not locate EDL prompt template. Searched: {paths_to_try}")


def upload_video_resumable(video_path, api_key, chunk_size_mb=64):
    """
    Upload video file using Google Generative Language Resumable File API.
    Uses chunked upload (default 64MB) with automatic exponential backoff retries and robust SSL.
    """
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)
    mime_type, _ = mimetypes.guess_type(video_path)
    mime_type = mime_type or "video/mp4"

    print(f"\n[Step 1/3] 📤 Uploading video to Gemini File API...")
    print(f"  • File Name  : {file_name} ({file_size / (1024 * 1024):.1f} MB)")
    print(f"  • Chunk Size : {chunk_size_mb} MB")
    print(f"  • MIME Type  : {mime_type}")

    ctx = get_ssl_context()

    # 1. Initial Resumable Upload Request
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json"
    }
    body = json.dumps({"file": {"display_name": file_name}}).encode("utf-8")

    req = urllib.request.Request(init_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            upload_url = resp.headers.get("X-Goog-Upload-URL")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Failed to initiate file upload: HTTP {e.code} - {err}")

    if not upload_url:
        raise RuntimeError("No upload URL returned by Gemini Resumable Upload API.")

    # 2. Upload in Binary Chunks with Exponential Backoff Auto-Retry
    CHUNK_SIZE = chunk_size_mb * 1024 * 1024
    print(f"  ► Uploading bytes in {chunk_size_mb}MB chunks...")
    t0 = time.time()
    offset = 0
    file_info = {}
    max_retries = 5

    with open(video_path, "rb") as f:
        while offset < file_size:
            chunk = f.read(CHUNK_SIZE)
            chunk_len = len(chunk)
            is_last = (offset + chunk_len) >= file_size

            command = "upload, finalize" if is_last else "upload"
            upload_headers = {
                "Content-Length": str(chunk_len),
                "X-Goog-Upload-Offset": str(offset),
                "X-Goog-Upload-Command": command
            }

            chunk_uploaded = False
            for attempt in range(max_retries):
                upload_req = urllib.request.Request(upload_url, data=chunk, headers=upload_headers, method="POST")
                try:
                    with urllib.request.urlopen(upload_req, context=ctx, timeout=120) as resp:
                        if is_last:
                            resp_data = json.loads(resp.read().decode("utf-8"))
                            file_info = resp_data.get("file", {})
                    chunk_uploaded = True
                    break
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                    if attempt < max_retries - 1:
                        sleep_sec = 2 ** attempt
                        print(f"\n  [Retry {attempt+1}/{max_retries}] Upload chunk at offset {offset / (1024 * 1024):.1f}MB failed ({e}). Retrying in {sleep_sec}s...")
                        time.sleep(sleep_sec)
                    else:
                        raise RuntimeError(f"Failed to upload video chunk at offset {offset} after {max_retries} attempts: {e}")

            offset += chunk_len
            pct = min(100.0, (offset / file_size) * 100.0)
            elapsed = time.time() - t0
            speed_mbps = (offset / (1024 * 1024)) / max(0.1, elapsed) * 8
            print(f"\r  ► Uploaded {offset / (1024 * 1024):.1f} / {file_size / (1024 * 1024):.1f} MB ({pct:.1f}% | {speed_mbps:.1f} Mbps)...", end="", flush=True)

    print()
    file_uri = file_info.get("uri")
    file_name_id = file_info.get("name")
    upload_time = time.time() - t0
    print(f"  ✓ Video uploaded in {upload_time:.1f}s (File ID: {file_name_id})")

    # 3. Wait for File Processing (ACTIVE state)
    print("  ► Waiting for Gemini video processing (ACTIVE status)...")
    get_file_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name_id}?key={api_key}"

    for attempt in range(60):
        try:
            with urllib.request.urlopen(get_file_url, context=ctx, timeout=15) as resp:
                check_data = json.loads(resp.read().decode("utf-8"))
                state = check_data.get("state", "PROCESSING")
                if state == "ACTIVE":
                    print(f"  ✓ Video state is ACTIVE and ready for inference.")
                    return file_uri, file_name_id
                elif state == "FAILED":
                    raise RuntimeError(f"Gemini file processing failed: {check_data.get('error')}")
                else:
                    time.sleep(3)
        except urllib.error.HTTPError:
            time.sleep(3)

    return file_uri, file_name_id


def generate_edl_content(file_uri, prompt_text, api_key, model="gemini-3.7-flash"):
    """Call Gemini generateContent API with video URI and prompt."""
    print(f"\n[Step 2/3] 🤖 Calling Gemini model: {model} ...")
    t0 = time.time()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
                    {"text": prompt_text}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    ctx = get_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - t0
            print(f"  ✓ Gemini response received in {elapsed:.1f}s")
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p.get("text", "") for p in parts if "text" in p]
                return "".join(text_parts).strip()
            return ""
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini generateContent failed (HTTP {e.code}): {err}")


def delete_remote_file(file_name_id, api_key):
    """Clean up remote uploaded video file on Gemini."""
    if not file_name_id:
        return
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name_id}?key={api_key}"
    req = urllib.request.Request(url, method="DELETE")
    ctx = get_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            print(f"  ✓ Remote video cleaned up: {file_name_id}")
    except Exception:
        pass


def extract_csv_from_markdown(text):
    """Extract CSV content from markdown code fence block."""
    matches = re.findall(r"```(?:csv)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    for m in matches:
        if "Start_Time" in m and "Best_Camera" in m:
            return m.strip()
    return None


def main():
    parser = argparse.ArgumentParser(
        description="AI Multimodal Video to EDL Decision Generator (Gemini / Codex / Local Models).",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-v", "--video", required=True, help="Path to input composite grid video (e.g. multicam_merged_part1.mp4)")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory for EDL CSV and report (default: same as input video)")
    parser.add_argument("-t", "--template", default=None, help="Custom prompt template file path")
    parser.add_argument("--model", default="gemini-3.7-flash", help="Multimodal model name (default: gemini-3.7-flash)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL for Codex or Local model endpoints")
    parser.add_argument("--api-key", default=None, help="API Key (or set GEMINI_API_KEY / OPENAI_API_KEY environment variable or .env file)")
    parser.add_argument("--upload-chunk-size", type=int, default=64, help="Upload chunk size in MB (default: 64)")
    parser.add_argument("--keep-remote", action="store_true", help="Keep uploaded video file on Gemini Files API")

    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"[Error] Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    api_key = resolve_api_key(args.api_key, args.base_url, args.model)
    if not api_key:
        print("\n[Error] Missing API Key!", file=sys.stderr)
        print("  Please provide a valid API key via one of the following methods:", file=sys.stderr)
        print("    1. CLI Argument : python3 scripts/generate_edl.py -v ... --api-key YOUR_KEY", file=sys.stderr)
        print("    2. Environment  : export GEMINI_API_KEY=\"AIzaSy...\"", file=sys.stderr)
        print("    3. Local File   : Add GEMINI_API_KEY=YOUR_KEY to .env or ~/.gemini/.env\n", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or os.path.dirname(os.path.abspath(args.video)) or "."
    os.makedirs(out_dir, exist_ok=True)

    video_basename = os.path.splitext(os.path.basename(args.video))[0]
    part_match = re.search(r"(part\d+)", video_basename, re.IGNORECASE)
    part_tag = f"_{part_match.group(1).lower()}" if part_match else ""

    edl_csv_path = os.path.join(out_dir, f"edl{part_tag}.csv")
    report_path = os.path.join(out_dir, f"edl{part_tag}_report.md")

    print("\n" + "=" * 78)
    print(f"🎬  Multimodal AI Video to EDL Generator (Model: {args.model})")
    print("=" * 78)
    print(f"  • Input Video : {args.video}")
    print(f"  • Model       : {args.model}")
    print(f"  • Target CSV  : {edl_csv_path}")
    print(f"  • Target Report: {report_path}")
    print("-" * 78)

    prompt_text = load_prompt_template(args.template)

    file_uri, file_name_id = upload_video_resumable(args.video, api_key, chunk_size_mb=args.upload_chunk_size)

    try:
        response_text = generate_edl_content(file_uri, prompt_text, api_key, model=args.model)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(response_text + "\n")
        print(f"\n[Step 3/3] 📄 Full AI Analysis Report saved to: {report_path}")

        csv_content = extract_csv_from_markdown(response_text)
        if csv_content:
            with open(edl_csv_path, "w", encoding="utf-8") as f:
                f.write(csv_content + "\n")
            print(f"  ✓ Extracted EDL CSV saved to: {edl_csv_path}")
        else:
            print(f"  [Warning] Could not extract CSV block from response. See full report: {report_path}", file=sys.stderr)

    finally:
        if not args.keep_remote and file_name_id:
            delete_remote_file(file_name_id, api_key)

    print("\n" + "=" * 78)
    print("✅  AI EDL Generation Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
