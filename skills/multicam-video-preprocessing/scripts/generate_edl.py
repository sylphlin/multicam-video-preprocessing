#!/usr/bin/env python3
"""
AI Multimodal Video to EDL Decision Generator (generate_edl.py).
Powered by Google Cloud Vertex AI (ADC) & Gemini 3.8 Flash Agentic Video Understanding
for zero-split full-length multicam video editing (>1 hour in a single pass).

Features:
  - 100% Vertex AI (ADC) & GCS Architecture: Uses Application Default Credentials
    and Google Cloud Storage (`gs://<bucket>/raw/`) with SHA-256 hash caching and
    2-day GCS Bucket Lifecycle auto-cleanup (plus optional `--cleanup-gcs`).
  - Agentic Video Understanding (Default): Uses goal-directed sparse temporal sampling,
    reducing token usage by 99.7% (~3k tokens for 1hr video) and eliminating split boundaries.
  - Broadcast-Grade EDL Prompt: Loads assets/edl_interview_template.md with zero-tolerance
    pre-roll / countdown elimination and asymmetric safety margin.
  - Deterministic EDL Semantic Validation: Built-in 8-rule structural validation (`--strict-edl`).
"""

import argparse
import csv
import os
import re
import sys
import time

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.llm_client import get_vertex_client
    from modules.gcp_client import (
        resolve_gcp_config,
        upload_file_to_gcs_with_cache,
        is_gdrive_source,
        transfer_gdrive_to_gcs_with_cache,
        delete_gcs_blob,
        guess_mime_type,
    )
    from modules.progress import LiveTicker
    from modules.edl_validator import (
        validate_edl_rows,
        format_validation_report,
        get_report_section_heading,
    )
except ImportError:
    from scripts.modules.llm_client import get_vertex_client
    from scripts.modules.gcp_client import (
        resolve_gcp_config,
        upload_file_to_gcs_with_cache,
        is_gdrive_source,
        transfer_gdrive_to_gcs_with_cache,
        delete_gcs_blob,
        guess_mime_type,
    )
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


def call_agentic_video_edl(gcs_uri, prompt_text, client, model="gemini-3.8-flash"):
    """
    Call Vertex AI Gemini using Agentic Video Understanding (processing="agentic").
    Uses google.genai client.interactions.create with fallback to client.models.generate_content.
    """
    from google.genai import types

    mime_type = guess_mime_type(gcs_uri)
    print(f"\n[Step 2/3] 🤖 Calling Vertex AI Agentic Video Understanding ({model}) ...")
    print(f"  • Video GCS URI : {gcs_uri}")
    print(f"  • Processing    : agentic (dynamic sparse sampling & sub-second retrieval)")
    t0 = time.time()

    raw_output = ""
    usage_info = {}

    with LiveTicker(f"Vertex AI Agentic Gemini ({model}) actively scanning video & computing EDL cuts"):
        try:
            # Primary: client.interactions.create
            interaction = client.interactions.create(
                model=model,
                input=[
                    {
                        "type": "video",
                        "uri": gcs_uri,
                        "mime_type": mime_type,
                        "processing": "agentic",
                    },
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                ],
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
            print(
                f"\n  ⚠️ interactions.create encountered: {e}. Trying client.models.generate_content with MediaProcessing.AGENTIC..."
            )
            try:
                # Fallback: client.models.generate_content with MediaProcessing.AGENTIC
                part = types.Part(
                    file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
                    media_processing=types.MediaProcessing.AGENTIC,
                )
                response = client.models.generate_content(
                    model=model,
                    contents=[part, prompt_text],
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=8192,
                    ),
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
                raise RuntimeError(
                    f"Both Vertex AI Agentic Video API methods failed: interactions.create ({e}), generate_content ({e2})"
                )

    duration = time.time() - t0
    print(f"  ✓ Agentic Video inference completed in {duration:.1f}s")
    if usage_info:
        print("  📊 Token Usage:")
        print(f"     - Input Tokens  : {usage_info.get('total_input_tokens', 0):,}")
        print(f"     - Output Tokens : {usage_info.get('total_output_tokens', 0):,}")
        if usage_info.get("total_thought_tokens"):
            print(f"     - Thought Tokens: {usage_info.get('total_thought_tokens', 0):,}")
        print(f"     - Total Tokens  : {usage_info.get('total_tokens', 0):,}")

    return raw_output, usage_info, duration


def generate_edl_content_standard(gcs_uri, prompt_text, client, model="gemini-3.8-flash"):
    """Standard Vertex AI multimodal generateContent call (1fps video sampling fallback)."""
    print(f"\n[Step 2/3] 🤖 Calling Vertex AI Gemini model: {model} (Standard Mode) ...")
    from google.genai import types

    mime_type = guess_mime_type(gcs_uri)
    t0 = time.time()
    with LiveTicker(f"Vertex AI Gemini ({model}) analyzing video & computing EDL cuts"):
        part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)
        response = client.models.generate_content(
            model=model,
            contents=[part, prompt_text],
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
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
        description="AI Multimodal Video to EDL Decision Generator (Vertex AI ADC + GCS + Gemini 3.8 Flash Agentic Video).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

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
                        help="Vertex AI Gemini model name (default: gemini-3.8-flash)")
    parser.add_argument("--processing", choices=["agentic", "standard"], default="agentic",
                        help="Video processing mode: 'agentic' (default, 99.7%% token savings, >1hr zero-split) or 'standard'")
    parser.add_argument("--project", default=None,
                        help="Google Cloud Project ID for Vertex AI / GCS (or set GOOGLE_CLOUD_PROJECT in .env)")
    parser.add_argument("--gcs-bucket", "--bucket", dest="gcs_bucket", default=None,
                        help="Google Cloud Storage Bucket for video staging (default: multicam-video-${PROJECT_ID})")
    parser.add_argument("--location", default=None,
                        help="Vertex AI Gemini endpoint location (default: global)")
    parser.add_argument("--region", default=None,
                        help="GCS Bucket infrastructure region (default: us-central1)")
    parser.add_argument("--force-upload", action="store_true",
                        help="Force re-upload of video file to GCS even if SHA-256 cache matches")
    parser.add_argument("--cleanup-gcs", action="store_true",
                        help="Immediately delete the staged video from GCS after EDL generation completes (otherwise governed by 2-day GCS Lifecycle)")
    parser.add_argument("--strict-edl", action="store_true",
                        help="EDL 驗證出現 ERROR 時中斷執行（預設僅警告並繼續）")
    parser.add_argument("--lang", default="en",
                        help="Language for the EDL validation report (default: en)")
    parser.add_argument("--edl-max-gap-sec", type=float, default=0.05,
                        help="EDL 鏡頭間隔容許門檻秒數 (預設: 0.05)")
    parser.add_argument("--edl-known-cameras", default=None,
                        help=r"EDL 預期已知相機列表，逗號分隔如 CAM1,CAM2 (預設: 自動推斷 ^CAM\d+$)")

    args = parser.parse_args()

    is_gdrive = is_gdrive_source(args.video)
    is_gcs_uri = str(args.video).startswith("gs://")
    if not is_gdrive and not is_gcs_uri and not os.path.exists(args.video):
        print(f"[Error] Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    gcp_cfg = resolve_gcp_config(
        cli_project=args.project,
        cli_bucket=args.gcs_bucket,
        cli_location=args.location,
        cli_region=args.region,
    )

    if not gcp_cfg.get("project") or not gcp_cfg.get("bucket"):
        print("\n[Error] Missing Google Cloud Project ID or GCS Bucket name!", file=sys.stderr)
        print(f"  Project : '{gcp_cfg.get('project')}'", file=sys.stderr)
        print(f"  Bucket  : '{gcp_cfg.get('bucket')}'", file=sys.stderr)
        print("  Action Required: Run './setup.sh' to auto-provision GCP & .env, or pass --project / --gcs-bucket.", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or ("." if (is_gdrive or is_gcs_uri) else (os.path.dirname(os.path.abspath(args.video)) or "."))
    os.makedirs(out_dir, exist_ok=True)

    edl_csv_path = args.output_csv or os.path.join(out_dir, "edl_full.csv")
    report_path = args.report or os.path.join(out_dir, "edl_full_report.md")

    print("\n" + "=" * 78)
    print(f"🎬  Multimodal AI Video to EDL Generator (Model: {args.model} | Mode: {args.processing.upper()})")
    print("=" * 78)
    print(f"  • Input Video    : {args.video}")
    print(f"  • Architecture   : {'Zero-Split Agentic Video (99.7% Token Reduction)' if args.processing == 'agentic' else 'Standard 1fps Multimodal'}")
    print(f"  • Active Backend : Google Cloud Vertex AI (Project: {gcp_cfg['project']}, Location: {gcp_cfg['location']})")
    print(f"  • GCS Storage    : gs://{gcp_cfg['bucket']}/raw/ (Region: {gcp_cfg['region']}, 2-day Lifecycle)")
    print(f"  • Target CSV     : {edl_csv_path}")
    print(f"  • Target Report  : {report_path}")
    print("-" * 78)

    if is_gcs_uri:
        video_uri = args.video
    elif is_gdrive:
        video_uri, _ = transfer_gdrive_to_gcs_with_cache(
            args.video,
            bucket_name=gcp_cfg["bucket"],
            gcs_prefix="raw",
            local_cache_dir=os.path.join(out_dir, "gdrive_inputs"),
            project=gcp_cfg["project"],
            region=gcp_cfg["region"],
            force=args.force_upload,
        )
    else:
        video_uri = upload_file_to_gcs_with_cache(
            args.video,
            bucket_name=gcp_cfg["bucket"],
            gcs_prefix="raw",
            project=gcp_cfg["project"],
            region=gcp_cfg["region"],
            force_upload=args.force_upload,
        )
    genai_client = get_vertex_client(
        project=gcp_cfg["project"],
        location=gcp_cfg["location"],
    )

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
                response_text, usage_info, duration = call_agentic_video_edl(
                    video_uri, prompt_text, client=genai_client, model=args.model
                )
            except Exception as e:
                print(
                    f"\n[Warning] Agentic processing encountered ({e}). Falling back to Vertex AI standard generateContent...",
                    file=sys.stderr,
                )
                response_text, usage_info, duration = generate_edl_content_standard(
                    video_uri, prompt_text, client=genai_client, model=args.model
                )
        else:
            response_text, usage_info, duration = generate_edl_content_standard(
                video_uri, prompt_text, client=genai_client, model=args.model
            )

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
        validation_result = validate_edl_rows(
            csv_rows, known_cameras=known_cams, max_gap_sec=args.edl_max_gap_sec, lang=args.lang
        )
        val_report_str = format_validation_report(validation_result, lang=args.lang)
        print(f"\n{val_report_str}")

        # Write CSV
        with open(edl_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)
        print("\n[Step 3/3] 📁 Saved outputs:")
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
            f"- **Backend**: `Google Cloud Vertex AI (ADC)`\n"
            f"- **Model**: `{args.model}`\n"
            f"- **Processing Mode**: `{args.processing.upper()}`\n"
            f"- **Inference Duration**: `{duration:.1f}s`\n"
        )
        if usage_info:
            token_section += f"- **Input Tokens**: `{usage_info.get('total_input_tokens', 0):,}`\n"
            token_section += f"- **Output Tokens**: `{usage_info.get('total_output_tokens', 0):,}`\n"
            if usage_info.get("total_thought_tokens"):
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
            print("\n[Error] EDL validation failed with errors under --strict-edl mode.", file=sys.stderr)
            sys.exit(1)

    finally:
        if args.cleanup_gcs and video_uri:
            delete_gcs_blob(video_uri, project=gcp_cfg.get("project"))

    print("\n" + "=" * 78)
    print("✅  AI EDL Generation Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
