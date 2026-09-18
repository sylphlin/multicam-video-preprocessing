#!/usr/bin/env python3
"""
AI Multimodal Video to EDL Decision Generator (generate_edl.py).
Powered by Gemini 3.8 Flash Agentic Video Understanding (processing="agentic")
for zero-split full-length multicam video editing (>1 hour in a single pass).

Features:
  - Agentic Video Understanding (Default): Uses goal-directed sparse temporal sampling,
    reducing token usage by 99.7% (~3k tokens for 1hr video) and eliminating split boundaries.
  - Resumable Chunked Upload & Server Cache: Automatically checks active server state
    via `.{video}_upload_cache.json` to skip re-uploading large video files.
  - Broadcast-Grade EDL Prompt: Loads assets/edl_interview_template.md with zero-tolerance
    pre-roll / countdown elimination and asymmetric safety margin.
  - Dual Mode Support: Native support for --processing agentic (default) and --processing standard.
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
    from modules.gcp_client import resolve_gcp_config, upload_file_to_gcs_with_cache, ensure_gcs_bucket
    from modules.progress import LiveTicker
    from modules.edl_validator import (
        validate_edl_rows,
        format_validation_report,
        get_report_section_heading,
    )
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key, get_ssl_context
    from scripts.modules.gcp_client import resolve_gcp_config, upload_file_to_gcs_with_cache, ensure_gcs_bucket
    from scripts.modules.progress import LiveTicker
    from scripts.modules.edl_validator import (
        validate_edl_rows,
        format_validation_report,
        get_report_section_heading,
    )


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
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
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


def call_agentic_video_edl(file_uri, prompt_text, client=None, api_key=None, model="gemini-3.8-flash"):
    """
    Call Gemini using Agentic Video Understanding (processing="agentic").
    Uses google.genai client.interactions.create with fallback to client.models.generate_content.
    """
    import google.genai as genai
    from google.genai import types

    if client is None:
        client = genai.Client(api_key=api_key)
    print(f"\n[Step 2/3] 🤖 Calling Agentic Video Understanding ({model}) ...")
    print(f"  • Video URI    : {file_uri}")
    print(f"  • Processing   : agentic (dynamic sparse sampling & sub-second retrieval)")
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


def generate_edl_content_standard(file_uri, prompt_text, client=None, api_key=None, model="gemini-3.8-flash"):
    """Standard multimodal generateContent call (1fps video sampling fallback)."""
    print(f"\n[Step 2/3] 🤖 Calling Gemini model: {model} (Standard Mode) ...")
    import google.genai as genai
    from google.genai import types

    if client is None:
        client = genai.Client(api_key=api_key)

    t0 = time.time()
    with LiveTicker(f"Gemini ({model}) analyzing video & computing EDL cuts"):
        part = types.Part(file_data=types.FileData(file_uri=file_uri, mime_type="video/mp4"))
        response = client.models.generate_content(
            model=model,
            contents=[part, prompt_text],
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192
            )
        )
    raw_output = response.text or ""
    usage_info = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage_info = {
            "total_input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
            "total_output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
            "total_thought_tokens": getattr(response.usage_metadata, "thoughts_token_count", 0),
            "total_tokens": getattr(response.usage_metadata, "total_token_count", 0),
        }
    duration = time.time() - t0
    return raw_output, usage_info, duration


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


def parse_edl_csv_and_report(raw_text):
    """
    Extract the CSV decision block and the Markdown analysis report from LLM output.
    """
    csv_rows = []

    # 1. Match CSV block inside ```csv ... ``` or raw CSV table
    csv_match = re.search(r"```(?:csv)?\s*\n(.*?)\n```", raw_text, re.DOTALL | re.IGNORECASE)
    csv_content = csv_match.group(1).strip() if csv_match else ""

    if not csv_content:
        # Fallback: search for header Start_Time,End_Time
        lines = raw_text.splitlines()
        capturing = False
        captured_lines = []
        for line in lines:
            if "Start_Time" in line and "Best_Camera" in line:
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

    # 2. Extract Report Markdown (remove csv block)
    report_markdown = re.sub(r"```(?:csv)?\s*\n.*?\n```", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    if not report_markdown:
        report_markdown = "# Multimodal AI EDL Analysis Report\n\nNo structured analysis commentary provided by model."

    return csv_rows, report_markdown


def main():
    parser = argparse.ArgumentParser(
        description="AI Multimodal Video to EDL Decision Generator (Gemini 3.8 Flash Agentic Video Understanding).",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-v", "--video", "-i", "--input", dest="video", required=True,
                        help="Path to composite grid video (e.g. multicam_merged_full.mp4)")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="Output directory for EDL CSV and report (default: same as input video)")
    parser.add_argument("--output-csv", default=None,
                        help="Custom destination path for EDL CSV file (optional)")
    parser.add_argument("--report", default=None,
                        help="Custom destination path for analysis report markdown (optional)")
    parser.add_argument("-t", "--template", default=None,
                        help="Custom prompt template file path (defaults to assets/edl_interview_template.md)")
    parser.add_argument("--model", default="gemini-3.8-flash",
                        help="Multimodal model name (default: gemini-3.8-flash)")
    parser.add_argument("--processing", choices=["agentic", "standard"], default="agentic",
                        help="Video processing mode: 'agentic' (default, 99.7%% token savings, >1hr zero-split) or 'standard'")
    parser.add_argument("--base-url", default=None,
                        help="OpenAI-compatible base URL for custom model endpoints")
    parser.add_argument("--backend", default="vertex", choices=["vertex", "studio"],
                        help="LLM & Storage backend: 'vertex' (Google Cloud Vertex AI + GCS via ADC, default) or 'studio' (Google AI Studio via API Key)")
    parser.add_argument("--project", default=None,
                        help="Google Cloud Project ID for Vertex AI / GCS (or set GOOGLE_CLOUD_PROJECT in .env)")
    parser.add_argument("--gcs-bucket", default=None,
                        help="Google Cloud Storage Bucket for video assets (or set GCS_BUCKET in .env)")
    parser.add_argument("--location", default=None,
                        help="Google Cloud Location/Region for Vertex AI (default: us-central1)")
    parser.add_argument("--fallback-studio", action="store_true",
                        help="Allow automatic fallback to Google AI Studio (API Key) if Vertex AI / GCS execution fails")
    parser.add_argument("--api-key", default=None,
                        help="API Key (or set GEMINI_API_KEY environment variable or .env file)")
    parser.add_argument("--upload-chunk-size", type=int, default=64,
                        help="Upload chunk size in MB (default: 64)")
    parser.add_argument("--force-upload", action="store_true",
                        help="Force re-upload of video file even if active cache exists")
    parser.add_argument("--keep-remote", action="store_true",
                        help="Keep uploaded video file on Gemini Files API")
    parser.add_argument("--strict-edl", action="store_true",
                        help="EDL 驗證出現 ERROR 時中斷執行（預設僅警告並繼續）")
    parser.add_argument("--lang", default="en",
                        help="Language for the EDL validation report (default: en)")
    parser.add_argument("--edl-max-gap-sec", type=float, default=0.05,
                        help="EDL 鏡頭間隔容許門檻秒數 (預設: 0.05)")
    parser.add_argument("--edl-known-cameras", default=None,
                        help=r"EDL 預期已知相機列表，逗號分隔如 CAM1,CAM2 (預設: 自動推斷 ^CAM\d+$)")

    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"[Error] Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    gcp_cfg = resolve_gcp_config(args.project, args.gcs_bucket, args.location)
    api_key = resolve_api_key(args.api_key, args.base_url, args.model)

    import google.genai as genai
    from google.genai import types

    out_dir = args.output_dir or os.path.dirname(os.path.abspath(args.video)) or "."
    os.makedirs(out_dir, exist_ok=True)

    edl_csv_path = args.output_csv or os.path.join(out_dir, "edl_full.csv")
    report_path = args.report or os.path.join(out_dir, "edl_full_report.md")

    print("\n" + "=" * 78)
    print(f"🎬  Multimodal AI Video to EDL Generator (Model: {args.model} | Mode: {args.processing.upper()})")
    print("=" * 78)
    print(f"  • Input Video  : {args.video}")
    print(f"  • Architecture : {'Zero-Split Agentic Video (99.7% Token Reduction)' if args.processing == 'agentic' else 'Standard 1fps Multimodal'}")
    print(f"  • Target CSV   : {edl_csv_path}")
    print(f"  • Target Report: {report_path}")

    video_uri = None
    file_name_id = None
    genai_client = None
    active_backend = args.backend

    if active_backend == "vertex":
        if not gcp_cfg.get("project") or not gcp_cfg.get("bucket"):
            if args.fallback_studio and api_key:
                print("\n  ⚠️ Incomplete GCP configuration (Project / Bucket). Falling back to Google AI Studio...", file=sys.stderr)
                active_backend = "studio"
            else:
                print("\n[Error] Missing Google Cloud Project ID or GCS Bucket name!", file=sys.stderr)
                print(f"  Project : '{gcp_cfg.get('project')}'", file=sys.stderr)
                print(f"  Bucket  : '{gcp_cfg.get('bucket')}'", file=sys.stderr)
                print("  Please configure GOOGLE_CLOUD_PROJECT and GCS_BUCKET in .env, pass --project/--gcs-bucket, or use --fallback-studio / --backend studio.", file=sys.stderr)
                sys.exit(1)

    if active_backend == "vertex":
        try:
            print(f"  • Active Backend : Google Cloud Vertex AI (Project: {gcp_cfg['project']}, Location: {gcp_cfg['location']})")
            print(f"  • GCS Storage    : gs://{gcp_cfg['bucket']}")
            print("-" * 78)
            video_uri = upload_file_to_gcs_with_cache(
                args.video,
                bucket_name=gcp_cfg["bucket"],
                project=gcp_cfg["project"],
                location=gcp_cfg["location"]
            )
            genai_client = genai.Client(
                vertexai=True,
                project=gcp_cfg["project"],
                location=gcp_cfg["location"]
            )
        except Exception as gcp_err:
            if args.fallback_studio and api_key:
                print(f"\n  ⚠️ Vertex AI / GCS preparation failed ({gcp_err}). Falling back to Google AI Studio...", file=sys.stderr)
                active_backend = "studio"
            else:
                raise RuntimeError(
                    f"Vertex AI / GCS operation failed on project '{gcp_cfg['project']}': {gcp_err}\n"
                    f"(Note: To permit automatic fallback to Google AI Studio, pass --fallback-studio)"
                )

    if active_backend == "studio":
        if not api_key:
            print("\n[Error] Missing Gemini API Key for Google AI Studio!", file=sys.stderr)
            print("  Please provide --api-key, export GEMINI_API_KEY, or set GEMINI_API_KEY in .env.", file=sys.stderr)
            sys.exit(1)
        print(f"  • Active Backend : Google AI Studio (API Key)")
        print("-" * 78)
        video_uri, file_name_id = upload_video_resumable(
            video_path=args.video,
            api_key=api_key,
            chunk_size_mb=args.upload_chunk_size,
            force_upload=args.force_upload
        )
        genai_client = genai.Client(api_key=api_key)

    prompt_text = load_prompt_template(args.template)

    # Full-length video instruction: ensure model knows timecode format covers >1hr without slicing
    prompt_text += (
        "\n\n---\n"
        "### 額外時間碼與全片長度特別指示：\n"
        "1. 本影片為完整全集錄影，時間碼格式請支援 `HH:MM:SS.000` 或 `MM:SS.000`（如 `01:02:15.000` 或 `62:15.000` 皆可）。\n"
        "2. 請由開頭 Global_Start_Time 一路分析覆蓋至全片結束 Global_End_Time，全片無切分斷句。\n"
    )

    try:
        if args.processing == "agentic":
            try:
                response_text, usage_info, duration = call_agentic_video_edl(video_uri, prompt_text, client=genai_client, model=args.model)
            except Exception as e:
                if active_backend == "vertex" and args.fallback_studio and api_key:
                    print(f"\n  ⚠️ Vertex AI Agentic execution encountered: {e}. Falling back to Google AI Studio...", file=sys.stderr)
                    s_uri, s_name = upload_video_resumable(args.video, api_key=api_key)
                    s_client = genai.Client(api_key=api_key)
                    response_text, usage_info, duration = call_agentic_video_edl(s_uri, prompt_text, client=s_client, model=args.model)
                else:
                    print(f"\n[Warning] Agentic processing failed ({e}). Falling back to standard generateContent...", file=sys.stderr)
                    response_text, usage_info, duration = generate_edl_content_standard(video_uri, prompt_text, client=genai_client, model=args.model)
        else:
            response_text, usage_info, duration = generate_edl_content_standard(video_uri, prompt_text, client=genai_client, model=args.model)

        # Parse CSV & Report
        csv_rows, report_md = parse_edl_csv_and_report(response_text)

        if not csv_rows:
            print("\n[Warning] Could not extract valid CSV rows from model output.", file=sys.stderr)
            raw_debug_path = edl_csv_path.replace(".csv", "_raw_output.txt")
            with open(raw_debug_path, "w", encoding="utf-8") as f:
                f.write(response_text)
            print(f"  • Raw model output saved to: {raw_debug_path}")
            sys.exit(1)

        # Validate EDL semantics
        known_cams = [c.strip() for c in args.edl_known_cameras.split(",") if c.strip()] if args.edl_known_cameras else None
        validation_result = validate_edl_rows(csv_rows, known_cameras=known_cams, max_gap_sec=args.edl_max_gap_sec, lang=args.lang)
        val_report_str = format_validation_report(validation_result, lang=args.lang)
        print(f"\n{val_report_str}")

        # Write CSV
        with open(edl_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)
        print(f"\n[Step 3/3] 📁 Saved outputs:")
        print(f"  ✓ EDL Decision CSV : {edl_csv_path} ({len(csv_rows) - 1} shot cuts)")

        # Write alias edl.csv if this is full cut
        if not args.output_csv:
            alias_csv = os.path.join(out_dir, "edl.csv")
            try:
                with open(alias_csv, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(csv_rows)
            except Exception:
                pass

        # Append usage metrics to report
        token_section = (
            f"\n\n---\n## 📊 Multimodal Performance Metrics\n"
            f"- **Model**: `{args.model}`\n"
            f"- **Processing Mode**: `{args.processing.upper()}`\n"
            f"- **Inference Duration**: `{duration:.1f}s`\n"
        )
        if usage_info:
            token_section += f"- **Input Tokens**: `{usage_info.get('total_input_tokens', 0):,}`\n"
            token_section += f"- **Output Tokens**: `{usage_info.get('total_output_tokens', 0):,}`\n"
            if usage_info.get('total_thought_tokens'):
                token_section += f"- **Thought Tokens**: `{usage_info.get('total_thought_tokens', 0):,}`\n"
            token_section += f"- **Total Tokens**: `{usage_info.get('total_tokens', 0):,}`\n"

        validation_section = (
            f"\n\n---\n## {get_report_section_heading(args.lang)}"
            f"\n```\n{val_report_str}\n```\n"
        )

        final_report = report_md + token_section + validation_section
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(final_report)
        print(f"  ✓ Analysis Report  : {report_path}")

        if validation_result.has_error and args.strict_edl:
            print(f"\n[Error] EDL validation failed with errors under --strict-edl mode.", file=sys.stderr)
            sys.exit(1)

    finally:
        if not args.keep_remote and file_name_id:
            # If cached, don't delete immediately unless force-upload was requested
            if args.force_upload:
                delete_remote_file(file_name_id, api_key)

    print("\n" + "=" * 78)
    print("✅  AI EDL Generation Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
