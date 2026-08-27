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
    from modules.llm_client import call_llm, resolve_api_key
except ImportError:
    from scripts.modules.llm_client import call_llm, resolve_api_key


DEFAULT_PROMPT_TEMPLATE_PATHS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "edl_interview_template.md"),
    os.path.expanduser("~/.gemini/config/skills/multicam-video-preprocessing/assets/edl_interview_template.md"),
    os.path.expanduser("~/.gemini/config/skills/gemini-edl-generation/assets/edl_interview_template.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "edl_interview_template.md"),
]


def get_api_key(cli_key=None):
    """Retrieve Gemini API key from CLI argument or environment variables."""
    if cli_key:
        return cli_key
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var)
        if val:
            return val
    return None


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


def upload_video_resumable(video_path, api_key):
    """
    Upload video file using Google Generative Language Resumable File API.
    Zero-dependency implementation using Python standard urllib.
    """
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)
    mime_type, _ = mimetypes.guess_type(video_path)
    mime_type = mime_type or "video/mp4"

    print(f"\n[Step 1/3] 📤 Uploading video to Gemini File API...")
    print(f"  • File Name: {file_name} ({file_size / (1024 * 1024):.1f} MB)")
    print(f"  • MIME Type: {mime_type}")

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
        with urllib.request.urlopen(req) as resp:
            upload_url = resp.headers.get("X-Goog-Upload-URL")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Failed to initiate file upload: HTTP {e.code} - {err}")

    if not upload_url:
        raise RuntimeError("No upload URL returned by Gemini Resumable Upload API.")

    # 2. Upload in 16MB Binary Chunks (Avoids 500MB single-payload limit)
    CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB chunks
    print("  ► Uploading bytes in 16MB chunks...")
    t0 = time.time()
    offset = 0
    file_info = {}

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
            upload_req = urllib.request.Request(upload_url, data=chunk, headers=upload_headers, method="POST")

            try:
                with urllib.request.urlopen(upload_req) as resp:
                    if is_last:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                        file_info = resp_data.get("file", {})
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Failed to upload video chunk at offset {offset}: HTTP {e.code} - {err}")

            offset += chunk_len
            pct = min(100.0, (offset / file_size) * 100.0)
            print(f"\r  ► Uploaded {offset / (1024 * 1024):.1f} / {file_size / (1024 * 1024):.1f} MB ({pct:.1f}%)...", end="", flush=True)

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
            with urllib.request.urlopen(get_file_url) as resp:
                check_data = json.loads(resp.read().decode("utf-8"))
                state = check_data.get("state", "PROCESSING")
                if state == "ACTIVE":
                    print(f"  ✓ Video state is ACTIVE and ready for inference.")
                    return file_uri, file_name_id
                elif state == "FAILED":
                    raise RuntimeError(f"Gemini file processing failed: {check_data.get(error)}")
                else:
                    time.sleep(3)
        except urllib.error.HTTPError as e:
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
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API inference failed: HTTP {e.code} - {err}")

    gen_time = time.time() - t0
    candidates = resp_data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"No candidates returned in Gemini response: {resp_data}")

    text_parts = candidates[0].get("content", {}).get("parts", [])
    full_text = "".join([p.get("text", "") for p in text_parts])
    print(f"  ✓ Gemini response generated in {gen_time:.1f}s ({len(full_text)} characters)")
    return full_text


def extract_csv_and_report(raw_text):
    """
    Extract the CSV decision block and full Markdown analysis report from Gemini response.
    """
    csv_match = re.search(r"```(?:csv)?\s*\n(Start_Time.*?)```", raw_text, re.DOTALL | re.IGNORECASE)
    if csv_match:
        csv_text = csv_match.group(1).strip()
    else:
        lines = []
        for line in raw_text.splitlines():
            line_str = line.strip()
            if "Start_Time" in line_str or re.match(r"^\d{2}:\d{2}\.\d{3},", line_str):
                lines.append(line_str)
        csv_text = "\n".join(lines).strip()

    return raw_text, csv_text


def main():
    parser = argparse.ArgumentParser(
        description="AI Multimodal Video to EDL Decision Generator: Analyze multi-camera video and produce EDL CSV & Trimming Report.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-v", "--video", required=True, help="Path to input video file (e.g. multicam_merged_part1.mp4)")
    parser.add_argument("-o", "--output", default=None, help="Output CSV path (default: [video_basename].csv)")
    parser.add_argument("-r", "--report", default=None, help="Output Markdown report path (default: [video_basename]_report.md)")
    parser.add_argument("-t", "--template", default=None, help="Path to custom prompt template asset markdown file")
    parser.add_argument("-m", "--model", default="gemini-3.7-flash", help="Multimodal model identifier (e.g. gemini-3.7-flash, gpt-5.6-luna, gemma4:e4b)")
    parser.add_argument("--base-url", default=None, help="Custom OpenAI-compatible API base URL (e.g. https://api.openai.com/v1, http://localhost:11434/v1)")
    parser.add_argument("-k", "--api-key", default=None, help="API Key (or set via GEMINI_API_KEY / OPENAI_API_KEY environment variables)")

    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key, args.base_url, args.model)
    if not api_key:
        print("[Error] Missing API Key! Please pass --api-key or set GEMINI_API_KEY / OPENAI_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.video):
        print(f"[Error] Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    # Resolve output paths (Option A: edl_part1.csv, edl_part1_report.md)
    base_name, _ = os.path.splitext(os.path.basename(args.video))
    out_dir = os.path.dirname(os.path.abspath(args.video))
    part_match = re.search(r"(part\d+)", base_name, re.IGNORECASE)
    part_tag = f"_{part_match.group(1).lower()}" if part_match else ""
    csv_out = args.output or os.path.join(out_dir, f"edl{part_tag}.csv")
    report_out = args.report or os.path.join(out_dir, f"edl{part_tag}_report.md")

    print("\n" + "=" * 78)
    print("🎬  AI Multi-Camera EDL Director (generate_edl.py)")
    print("=" * 78)
    print(f"  • Video Input   : {args.video}")
    print(f"  • Model Choice  : {args.model}")
    if args.base_url:
        print(f"  • Base URL      : {args.base_url}")
    print(f"  • CSV Output    : {csv_out}")
    print(f"  • Report Output : {report_out}")
    print("-" * 78)

    prompt_template = load_prompt_template(args.template)

    try:
        if not args.base_url and "gemini" in args.model.lower():
            file_uri, file_id = upload_video_resumable(args.video, api_key)
            raw_response = call_llm(prompt_template, model=args.model, file_uri=file_uri, api_key=api_key)
        else:
            raw_response = call_llm(prompt_template, model=args.model, base_url=args.base_url, api_key=api_key)

        full_report, csv_content = extract_csv_and_report(raw_response)

        print(f"\n[Step 3/3] 💾 Saving EDL CSV and Analysis Report...")
        with open(report_out, "w", encoding="utf-8") as f:
            f.write(full_report.strip() + "\n")
        print(f"  ✓ Saved Trimming & Role Calibration Report: {report_out}")

        with open(csv_out, "w", encoding="utf-8") as f:
            f.write(csv_content.strip() + "\n")
        print(f"  ✓ Saved EDL CSV Decisions: {csv_out}")

        print("\n" + "=" * 78)
        print("✅  AI EDL Generation Completed Successfully!")
        print("=" * 78 + "\n")

    except Exception as e:
        print(f"\n[Error] EDL generation failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
