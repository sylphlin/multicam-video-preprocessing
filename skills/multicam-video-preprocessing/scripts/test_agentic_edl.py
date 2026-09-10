#!/usr/bin/env python3
"""
Agentic Video Multimodal EDL Decision Generator (test_agentic_edl.py).

Uses Gemini 3.7 Flash Agentic Video Understanding (processing="agentic") to generate
a unified full-length EDL decision table and trimming report for uncut >1hr multicam grid videos
without requiring chapter segmentation (Zero-Split Pipeline).

Features:
  - Resumable chunked upload with upload caching (.upload_cache.json)
  - Google GenAI Client with client.interactions.create(processing="agentic")
  - Fallback to client.models.generate_content(media_processing="AGENTIC")
  - Accurate token usage & modality tracking
  - Parses standard EDL CSV and Markdown report
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
    from modules.llm_client import resolve_api_key, get_ssl_context
    from modules.progress import LiveTicker
except ImportError:
    from scripts.modules.llm_client import resolve_api_key, get_ssl_context
    from scripts.modules.progress import LiveTicker


DEFAULT_PROMPT_TEMPLATE_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "edl_interview_template.md"),
    os.path.expanduser("~/.gemini/config/plugins/multicam-video-preprocessing/skills/multicam-video-preprocessing/assets/edl_interview_template.md"),
    os.path.expanduser("~/.codex/plugins/multicam-video-preprocessing/skills/multicam-video-preprocessing/assets/edl_interview_template.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "edl_interview_template.md"),
    os.path.join(os.getcwd(), "assets", "edl_interview_template.md"),
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


def upload_video_resumable(video_path, api_key, chunk_size_mb=64, force_upload=False):
    """
    Upload video file using Google Generative Language Resumable File API.
    Checks cache first to avoid re-uploading large (>1GB) files.
    """
    cache_path = os.path.join(os.path.dirname(os.path.abspath(video_path)), f".{os.path.basename(video_path)}_upload_cache.json")
    ctx = get_ssl_context()

    if not force_upload and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            file_name_id = cached.get("file_name_id")
            file_uri = cached.get("file_uri")
            
            # Verify file still exists and is ACTIVE on Google server
            check_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name_id}?key={api_key}"
            with urllib.request.urlopen(check_url, context=ctx, timeout=15) as resp:
                check_data = json.loads(resp.read().decode("utf-8"))
                if check_data.get("state") == "ACTIVE":
                    print(f"  ✓ Reusing cached active upload: {file_name_id} ({file_uri})")
                    return file_uri, file_name_id
        except Exception:
            pass  # Cache invalid or expired, proceed to upload

    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)
    mime_type, _ = mimetypes.guess_type(video_path)
    mime_type = mime_type or "video/mp4"

    print(f"\n[Step 1/3] 📤 Uploading video to Gemini File API...")
    print(f"  • File Name  : {file_name} ({file_size / (1024 * 1024):.1f} MB)")
    print(f"  • Chunk Size : {chunk_size_mb} MB")
    print(f"  • MIME Type  : {mime_type}")

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

    with open(video_path, "rb") as f:
        while offset < file_size:
            chunk = f.read(CHUNK_SIZE)
            chunk_len = len(chunk)
            is_last = (offset + chunk_len) >= file_size
            command = "upload, finalize" if is_last else "upload"

            chunk_headers = {
                "X-Goog-Upload-Command": command,
                "X-Goog-Upload-Offset": str(offset),
                "Content-Length": str(chunk_len)
            }

            max_retries = 5
            for attempt in range(max_retries):
                try:
                    chunk_req = urllib.request.Request(upload_url, data=chunk, headers=chunk_headers, method="POST")
                    with urllib.request.urlopen(chunk_req, context=ctx, timeout=120) as resp:
                        resp_data = resp.read().decode("utf-8", errors="ignore")
                        if is_last and resp_data:
                            try:
                                file_info = json.loads(resp_data).get("file", {})
                            except Exception:
                                pass
                    break
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                    if attempt < max_retries - 1:
                        sleep_sec = 2 ** (attempt + 1)
                        print(f"\n  ⚠️ Chunk upload failed ({e}), retrying in {sleep_sec}s...")
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
    print("  ► Waiting for Gemini video indexing (ACTIVE status)...")
    get_file_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name_id}?key={api_key}"
    t_wait_start = time.time()

    for attempt in range(120):
        try:
            with urllib.request.urlopen(get_file_url, context=ctx, timeout=15) as resp:
                check_data = json.loads(resp.read().decode("utf-8"))
                state = check_data.get("state", "PROCESSING")
                elapsed_wait = time.time() - t_wait_start
                if state == "ACTIVE":
                    print(f"\r  ✓ Video state is ACTIVE and ready for inference ({elapsed_wait:.1f}s).          \n", flush=True)
                    # Cache upload
                    try:
                        with open(cache_path, "w", encoding="utf-8") as cf:
                            json.dump({"file_name_id": file_name_id, "file_uri": file_uri, "timestamp": time.time()}, cf)
                    except Exception:
                        pass
                    return file_uri, file_name_id
                elif state == "FAILED":
                    raise RuntimeError(f"Gemini file processing failed: {check_data.get('error')}")
                else:
                    print(f"\r  ► ⏳ Google server video indexing... [Elapsed: {elapsed_wait:.0f}s | Status: {state}]", end="", flush=True)
                    time.sleep(3)
        except urllib.error.HTTPError:
            elapsed_wait = time.time() - t_wait_start
            print(f"\r  ► ⏳ Google server video indexing... [Elapsed: {elapsed_wait:.0f}s | Polling...]", end="", flush=True)
            time.sleep(3)

    return file_uri, file_name_id


def call_agentic_video_edl(file_uri, prompt_text, api_key, model="gemini-3.7-flash"):
    """
    Call Gemini using Agentic Video Understanding (processing="agentic").
    Uses google.genai client.interactions.create with fallback to client.models.generate_content.
    """
    import google.genai as genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    print(f"\n[Step 2/3] 🤖 Calling Agentic Video Understanding ({model}) ...")
    print(f"  • Video URI    : {file_uri}")
    print(f"  • Processing   : agentic (dynamic inspection & sub-second retrieval)")
    t0 = time.time()

    raw_output = ""
    usage_info = {}

    with LiveTicker(f"Agentic Gemini ({model}) actively scanning video & computing EDL cuts"):
        try:
            # Primary: client.interactions.create
            interaction = client.interactions.create(
                model=model,
                input=[
                    {
                        "type": "video",
                        "uri": file_uri,
                        "processing": "agentic"
                    },
                    {
                        "type": "text",
                        "text": prompt_text
                    }
                ]
            )
            raw_output = interaction.output_text or ""
            if hasattr(interaction, "usage") and interaction.usage:
                usage_info = {
                    "total_input_tokens": getattr(interaction.usage, "total_input_tokens", 0),
                    "total_output_tokens": getattr(interaction.usage, "total_output_tokens", 0),
                    "total_thought_tokens": getattr(interaction.usage, "total_thought_tokens", 0),
                    "total_tokens": getattr(interaction.usage, "total_tokens", 0),
                }
        except Exception as e:
            print(f"\n  ⚠️ interactions.create encountered: {e}. Trying client.models.generate_content with MediaProcessing.AGENTIC...")
            try:
                # Fallback: client.models.generate_content
                part = types.Part(
                    file_data=types.FileData(file_uri=file_uri, mime_type="video/mp4"),
                    media_processing=types.MediaProcessing.AGENTIC
                )
                response = client.models.generate_content(
                    model=model,
                    contents=[part, prompt_text],
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=8192
                    )
                )
                raw_output = response.text or ""
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    usage_info = {
                        "total_input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                        "total_output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
                        "total_thought_tokens": getattr(response.usage_metadata, "thoughts_token_count", 0),
                        "total_tokens": getattr(response.usage_metadata, "total_token_count", 0),
                    }
            except Exception as e2:
                raise RuntimeError(f"Both Agentic Video API methods failed: interactions.create ({e}), generate_content ({e2})")

    duration = time.time() - t0
    print(f"  ✓ Agentic Video inference completed in {duration:.1f}s")
    if usage_info:
        print(f"  📊 Token Usage:")
        print(f"     - Input Tokens  : {usage_info.get('total_input_tokens', 0):,}")
        print(f"     - Output Tokens : {usage_info.get('total_output_tokens', 0):,}")
        if usage_info.get('total_thought_tokens'):
            print(f"     - Thought Tokens: {usage_info.get('total_thought_tokens', 0):,}")
        print(f"     - Total Tokens  : {usage_info.get('total_tokens', 0):,}")

    return raw_output, usage_info, duration


def parse_edl_csv_and_report(raw_text):
    """
    Extract the CSV decision block and the Markdown analysis report from LLM output.
    """
    csv_rows = []
    report_markdown = ""

    # 1. Match CSV block inside ```csv ... ``` or raw CSV table
    csv_match = re.search(r"```csv\s*\n(.*?)\n```", raw_text, re.DOTALL)
    csv_content = csv_match.group(1).strip() if csv_match else ""

    if not csv_content:
        # Fallback: search for header Start_Time,End_Time
        lines = raw_text.splitlines()
        capturing = False
        captured_lines = []
        for line in lines:
            if "Start_Time" in line and "End_Time" in line and "Best_Camera" in line:
                capturing = True
                captured_lines.append(line)
                continue
            if capturing:
                if re.match(r"^\d{2}:\d{2}", line.strip()):
                    captured_lines.append(line)
                elif line.strip() == "" or line.startswith("#"):
                    break
        if captured_lines:
            csv_content = "\n".join(captured_lines)

    if csv_content:
        reader = csv.reader(csv_content.splitlines())
        for row in reader:
            if row:
                csv_rows.append(row)

    # 2. Extract Report Markdown
    # Remove the csv block and keep the rest
    report_markdown = re.sub(r"```csv\s*\n.*?\n```", "", raw_text, flags=re.DOTALL).strip()
    if not report_markdown:
        report_markdown = "# Agentic Video EDL Analysis Report\n\nNo structured analysis commentary provided by model."

    return csv_rows, report_markdown


def main():
    parser = argparse.ArgumentParser(
        description="Agentic Video Multimodal EDL Decision Generator for Full Multicam Videos."
    )
    parser.add_argument("-i", "--input", default="output/grid_agentic_full.mp4",
                        help="Path to full-length multi-camera grid video (default: output/grid_agentic_full.mp4)")
    parser.add_argument("-o", "--output-csv", default="output/edl_agentic_full.csv",
                        help="Path to output EDL CSV file (default: output/edl_agentic_full.csv)")
    parser.add_argument("--report", default="output/edl_agentic_full_report.md",
                        help="Path to output analysis report markdown (default: output/edl_agentic_full_report.md)")
    parser.add_argument("--model", default="gemini-3.7-flash",
                        help="Gemini multimodal model with agentic video support (default: gemini-3.7-flash)")
    parser.add_argument("--template", default=None,
                        help="Path to custom EDL prompt template (defaults to assets/edl_interview_template.md)")
    parser.add_argument("--api-key", default=None,
                        help="Google Gemini API key (defaults to resolving from environment or .env)")
    parser.add_argument("--force-upload", action="store_true",
                        help="Force re-upload of video file even if cached on server")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input video not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    api_key = resolve_api_key(args.api_key, model=args.model)
    if not api_key:
        print("Error: Could not resolve Google Gemini API Key. Set GEMINI_API_KEY or use --api-key.", file=sys.stderr)
        sys.exit(1)

    prompt_template = load_prompt_template(args.template)
    prompt_template += (
        "\n\n---\n"
        "### 額外時間碼與全片長度特別指示：\n"
        "1. 本影片為完整未分割之多機位錄影，全長約 64 分 35 秒（~3867 秒）。\n"
        "2. 時間碼格式請支援 `HH:MM:SS.000` 或 `MM:SS.000`（例如 `01:02:15.000` 或 `62:15.000` 皆可）。\n"
        "3. 請由開頭 Global_Start_Time 一路分析覆蓋至全片結束 Global_End_Time，不可截斷或提早結束。\n"
    )

    # Step 1: Upload video to Gemini File API
    file_uri, file_name_id = upload_video_resumable(
        video_path=args.input,
        api_key=api_key,
        chunk_size_mb=64,
        force_upload=args.force_upload
    )

    # Step 2: Call Agentic Video Understanding
    raw_output, usage_info, duration = call_agentic_video_edl(
        file_uri=file_uri,
        prompt_text=prompt_template,
        api_key=api_key,
        model=args.model
    )

    # Step 3: Parse CSV & Report
    csv_rows, report_md = parse_edl_csv_and_report(raw_output)

    if not csv_rows:
        print("\n[Warning] Could not extract valid CSV rows from model output.", file=sys.stderr)
        raw_debug_path = args.output_csv.replace(".csv", "_raw_output.txt")
        with open(raw_debug_path, "w", encoding="utf-8") as f:
            f.write(raw_output)
        print(f"  • Raw model output saved to: {raw_debug_path}")
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_csv)), exist_ok=True)
    with open(args.output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)

    print(f"\n[Step 3/3] 📁 Saved outputs:")
    print(f"  ✓ EDL Decision CSV : {args.output_csv} ({len(csv_rows) - 1} shot cuts)")

    # Append usage metrics to report
    token_section = f"\n\n---\n## 📊 Agentic Video Performance Metrics\n- **Model**: `{args.model}`\n- **Inference Duration**: `{duration:.1f}s`\n"
    if usage_info:
        token_section += f"- **Input Tokens**: `{usage_info.get('total_input_tokens', 0):,}`\n"
        token_section += f"- **Output Tokens**: `{usage_info.get('total_output_tokens', 0):,}`\n"
        if usage_info.get('total_thought_tokens'):
            token_section += f"- **Thought Tokens**: `{usage_info.get('total_thought_tokens', 0):,}`\n"
        token_section += f"- **Total Tokens**: `{usage_info.get('total_tokens', 0):,}`\n"

    final_report = report_md + token_section
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(final_report)
    print(f"  ✓ Analysis Report  : {args.report}")

    print("\n🎉 Agentic Video EDL Generation Finished Successfully!")


if __name__ == "__main__":
    main()
