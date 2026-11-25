#!/usr/bin/env python3
"""
AI Multimodal Video to EDL Decision Generator (generate_edl.py).
Powered by Google Cloud Vertex AI (ADC) and Gemini 4 Hydrogen Multimodal Video Understanding.

Features:
  - 100% Vertex AI (ADC) & GCS Architecture: Uses Application Default Credentials
    and Google Cloud Storage (`gs://<bucket>/raw/`) with SHA-256 hash caching and
    2-day GCS Bucket Lifecycle auto-cleanup (plus optional `--cleanup-gcs`).
  - Silence-Aware Smart Segmentation (30-40 min windows): Automatically detects natural
    speech pauses via FFmpeg silencedetect + RMS energy minimum for videos exceeding
    40 minutes, slices temporary chunks in `<output_dir>/_edl_chunks/`, runs parallel
    inference, shifts and stitches timestamps into a single `edl_full.csv`, and deletes
    all temporary local and GCS chunks in a `finally` block.
  - Standard Low-Res Multimodal Mode (Default): Uses `MEDIA_RESOLUTION_LOW` and dynamic
    `thinking_level` for fast, timeout-free inference.
  - Deterministic EDL Semantic Validation: Built-in 8-rule structural validation (`--strict-edl`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import os
import re
import shutil
import subprocess
import sys
import time

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.llm_client import get_vertex_client, resolve_thinking_level
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
    from modules.video_segmenter import (
        find_natural_split_points,
        slice_video_into_temp_chunks,
        shift_and_merge_chunk_edl_rows,
        format_edl_timestamp,
    )
    from modules.time_utils import ensure_ffmpeg_in_path
except ImportError:
    from scripts.modules.llm_client import get_vertex_client, resolve_thinking_level
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
    from scripts.modules.video_segmenter import (
        find_natural_split_points,
        slice_video_into_temp_chunks,
        shift_and_merge_chunk_edl_rows,
        format_edl_timestamp,
    )
    from scripts.modules.time_utils import ensure_ffmpeg_in_path


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


def _probe_video_duration_sec(video_path: str) -> float:
    """Probe duration of a local video file in seconds using ffprobe."""
    if not video_path or not os.path.isfile(video_path):
        return 0.0
    ensure_ffmpeg_in_path()
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception:
        pass
    return 0.0


def calculate_dynamic_thinking_budget(video_duration_seconds: float) -> int:
    """
    Calculate the dynamic thinking token budget from the video duration.
    Balance multi-camera cut reasoning with the 300-second gateway deadline.
    """
    est_cuts = max(10.0, float(video_duration_seconds) / 25.0)
    cognitive_demand = 1024 + int(est_cuts * 45.0)
    est_prefill_sec = min(145.0, 15.0 + max(0.0, float(video_duration_seconds)) * 0.055)
    est_output_sec = 25.0
    available_thinking_sec = max(25.0, 220.0 - est_prefill_sec - est_output_sec)
    safe_max_tokens = int(available_thinking_sec * 42.0)
    target_budget = min(cognitive_demand, safe_max_tokens)
    return max(1024, min(target_budget, 4096))


def _build_chunk_prompt(base_prompt: str, part_index: int, total_parts: int, start_sec: float, end_sec: float) -> str:
    """
    Append segment-specific boundary instructions to the base EDL prompt.
    """
    dur_sec = max(0.0, end_sec - start_sec)
    dur_str = format_edl_timestamp(dur_sec)
    global_start_str = format_edl_timestamp(start_sec)
    global_end_str = format_edl_timestamp(end_sec)

    if total_parts <= 1:
        return (
            base_prompt
            + "\n\n---\n"
            + "### 額外時間碼與全片長度特別指示：\n"
            + f"1. 本影片總長為 `{dur_str}`，時間碼格式請支援 `HH:MM:SS.000` 或 `MM:SS.000`。\n"
            + "2. 請由開頭 Global_Start_Time 一路分析覆蓋至全片結束 Global_End_Time。\n"
        )

    if part_index == 1:
        role_note = (
            f"1. 本段為全片第 {part_index}/{total_parts} 段（對應母片區間 `{global_start_str}` 至 `{global_end_str}`，本段片長 `{dur_str}`）。\n"
            "2. **時間碼基準**：請以本段影片自身的相對時間碼（由 `00:00.000` 起算至 `" + dur_str + "`）輸出 CSV。\n"
            "3. **片頭與結尾規則**：請在開頭套用【規則 0】裁除開拍前倒數與準備廢料；但本段結尾是訪談中段的自然換氣切點（並非節目收工），**請務必一路剪輯覆蓋至本段結尾 `" + dur_str + "`，切勿提早截斷結尾**。\n"
        )
    elif part_index == total_parts:
        role_note = (
            f"1. 本段為全片第 {part_index}/{total_parts} 段（最後一段，對應母片區間 `{global_start_str}` 至 `{global_end_str}`，本段片長 `{dur_str}`）。\n"
            "2. **時間碼基準**：請以本段影片自身的相對時間碼（由 `00:00.000` 起算至 `" + dur_str + "`）輸出 CSV。\n"
            "3. **片頭與結尾規則**：本段開頭緊接上一段訪談，**第一筆鏡頭請直接從 `00:00.000` 開始（切勿當成片頭廢料裁除）**；並在節目結尾道別後套用【規則 0】裁除收工關機前廢料。\n"
        )
    else:
        role_note = (
            f"1. 本段為全片第 {part_index}/{total_parts} 段（中段，對應母片區間 `{global_start_str}` 至 `{global_end_str}`，本段片長 `{dur_str}`）。\n"
            "2. **時間碼基準**：請以本段影片自身的相對時間碼（由 `00:00.000` 起算至 `" + dur_str + "`）輸出 CSV。\n"
            "3. **片頭與結尾規則**：本段為訪談進行中段落，**請直接從 `00:00.000` 一路連續剪輯覆蓋至本段結尾 `" + dur_str + "`**，頭尾皆不裁除。\n"
        )

    return base_prompt + "\n\n---\n### 分段時間碼與邊界銜接特別指示：\n" + role_note


def _extract_visible_text(response):
    """
    Extract non-thought text parts from a generate_content response.
    """
    if not response:
        return ""
    candidates = getattr(response, "candidates", None)
    if candidates and len(candidates) > 0:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            visible = [
                getattr(p, "text", "")
                for p in parts
                if isinstance(getattr(p, "text", None), str)
                and getattr(p, "text", "")
                and getattr(p, "thought", False) is not True
            ]
            if visible:
                return "\n".join(visible).strip()
    try:
        if getattr(response, "text", None):
            return response.text.strip()
    except Exception:
        pass
    return ""


def ensure_agentic_ready_video(video_path, max_size_mb=900.0, encoder="h264_videotoolbox"):
    """
    Verify that a local composite grid video is compact and indexed with short GOP for GCS random seeking.
    If the file size exceeds max_size_mb, transcode a lightweight short-GOP (+faststart, 10 fps, -g 10, 1200k)
    proxy file in the same directory and return its path.
    """
    if not video_path or not os.path.isfile(video_path):
        return video_path

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= max_size_mb:
        return video_path

    base, _ = os.path.splitext(video_path)
    proxy_path = f"{base}_agentic_opt.mp4"
    if os.path.isfile(proxy_path):
        proxy_mb = os.path.getsize(proxy_path) / (1024 * 1024)
        if 0 < proxy_mb <= max_size_mb:
            print(f"  • Using cached lightweight video proxy: {proxy_path} ({proxy_mb:.1f} MB)")
            return proxy_path

    print(
        f"  • Input video is {size_mb:.1f} MB (> {max_size_mb:.0f} MB). "
        f"Transcoding short-GOP faststart proxy (10 fps, -g 10, 1200k)..."
    )
    ensure_ffmpeg_in_path()
    t0 = time.time()
    for enc in ([encoder, "libx264"] if encoder != "libx264" else ["libx264"]):
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-c:v", enc,
            "-b:v", "1200k",
            "-r", "10",
            "-g", "10",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "64k",
            "-movflags", "+faststart",
            proxy_path,
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and os.path.isfile(proxy_path):
            new_mb = os.path.getsize(proxy_path) / (1024 * 1024)
            print(
                f"  ✓ Short-GOP proxy ready in {time.time() - t0:.1f}s: "
                f"{proxy_path} ({size_mb:.1f} MB -> {new_mb:.1f} MB)"
            )
            return proxy_path

    return video_path


def call_agentic_video_edl(
    gcs_uri,
    prompt_text,
    client,
    model="gemini-4-hydrogen",
    max_output_tokens=65536,
    thinking_budget=4096,
    timeout_sec=600.0,
):
    """
    Call Vertex AI Gemini with Agentic Video Understanding (`processing="agentic"`).
    Use `client.interactions.create` with an explicit timeout and `max_output_tokens`,
    and fall back to `client.models.generate_content` (`MediaProcessing.AGENTIC`) on error.
    """
    from google.genai import types

    mime_type = guess_mime_type(gcs_uri)
    print(f"\n[Step 2/3] 🤖 Calling Vertex AI Agentic Video Understanding ({model}) ...")
    print(f"  • Video GCS URI : {gcs_uri}")
    print(f"  • Processing    : agentic (dynamic sparse sampling & sub-second retrieval, timeout={int(timeout_sec)}s)")
    t0 = time.time()

    raw_output = ""
    usage_info = {}

    with LiveTicker(f"Vertex AI Agentic Gemini ({model}) actively scanning video & computing EDL cuts"):
        try:
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
                generation_config={
                    "max_output_tokens": max_output_tokens,
                },
                timeout=float(timeout_sec),
            )
            raw_output = interaction.output_text or ""
            if hasattr(interaction, "usage") and interaction.usage:
                usage_info = {
                    "total_input_tokens": getattr(interaction.usage, "total_input_tokens", 0) or 0,
                    "total_output_tokens": getattr(interaction.usage, "total_output_tokens", 0) or 0,
                    "total_thought_tokens": getattr(interaction.usage, "total_thought_tokens", 0) or 0,
                    "total_tool_use_tokens": getattr(interaction.usage, "total_tool_use_tokens", 0) or 0,
                    "total_tokens": getattr(interaction.usage, "total_tokens", 0) or 0,
                }
        except Exception as e:
            print(
                f"\n  ⚠️ interactions.create encountered: {e}. "
                f"Trying client.models.generate_content with MediaProcessing.AGENTIC..."
            )
            try:
                part = types.Part(
                    file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
                    media_processing=types.MediaProcessing.AGENTIC,
                )
                is_gemini_4 = "gemini-4" in str(model).lower()
                resolved_budget = int(thinking_budget) if thinking_budget is not None else None
                cfg_kwargs = {
                    "max_output_tokens": max_output_tokens,
                }
                if is_gemini_4:
                    thinking_level = resolve_thinking_level(resolved_budget)
                    print(f"  • Fallback config: model={model}, thinking_level={thinking_level}")
                    if hasattr(types, "ThinkingConfig"):
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
                else:
                    cfg_kwargs["temperature"] = 0.2
                    if resolved_budget is not None and hasattr(types, "ThinkingConfig"):
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=resolved_budget)
                if hasattr(types, "AutomaticFunctionCallingConfig"):
                    cfg_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)

                response = client.models.generate_content(
                    model=model,
                    contents=[part, prompt_text],
                    config=types.GenerateContentConfig(**cfg_kwargs),
                )
                raw_output = _extract_visible_text(response)
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    usage_info = {
                        "total_input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                        "total_output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
                        "total_thought_tokens": getattr(response.usage_metadata, "thoughts_token_count", 0) or 0,
                        "total_tool_use_tokens": getattr(response.usage_metadata, "tool_use_prompt_token_count", 0) or 0,
                        "total_tokens": getattr(response.usage_metadata, "total_token_count", 0) or 0,
                    }
            except Exception as e2:
                raise RuntimeError(
                    f"Both Vertex AI Agentic Video API methods failed: interactions.create ({e}), generate_content ({e2})"
                )

    duration = time.time() - t0
    print(f"  ✓ Agentic Video inference completed in {duration:.1f}s")
    return raw_output, usage_info, duration


def generate_edl_content_standard(
    gcs_uri,
    prompt_text,
    client,
    model="gemini-4-hydrogen",
    max_output_tokens=65536,
    thinking_budget=None,
    video_duration_sec=1800.0,
    label_prefix="",
    use_ticker=True,
):
    """
    Run standard Vertex AI multimodal `generate_content` with `MEDIA_RESOLUTION_LOW`.
    Use `thinking_level` on Gemini 4 models and `thinking_budget` on legacy 3.x models.
    """
    from google.genai import types

    resolved_budget = (
        int(thinking_budget)
        if thinking_budget is not None
        else calculate_dynamic_thinking_budget(video_duration_sec)
    )
    is_gemini_4 = "gemini-4" in str(model).lower()
    tag = f"[{label_prefix}] " if label_prefix else ""

    mime_type = guess_mime_type(gcs_uri)
    t0 = time.time()
    cfg_kwargs = {
        "max_output_tokens": max_output_tokens,
    }
    if hasattr(types, "MediaResolution"):
        cfg_kwargs["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_LOW

    if is_gemini_4:
        thinking_level = resolve_thinking_level(resolved_budget)
        print(
            f"  ► {tag}Calling Vertex AI Gemini ({model}, Standard Low-Res, "
            f"thinking_level={thinking_level}) -> {gcs_uri}"
        )
        if hasattr(types, "ThinkingConfig"):
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    else:
        cfg_kwargs["temperature"] = 0.1
        print(
            f"  ► {tag}Calling Vertex AI Gemini ({model}, Standard Low-Res, "
            f"thinking_budget={resolved_budget} tokens) -> {gcs_uri}"
        )
        if hasattr(types, "ThinkingConfig"):
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=resolved_budget)

    if hasattr(types, "AutomaticFunctionCallingConfig"):
        cfg_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)

    part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)
    if use_ticker:
        with LiveTicker(f"{tag}Vertex AI Gemini ({model}) analyzing video & computing EDL cuts"):
            response = client.models.generate_content(
                model=model,
                contents=[part, prompt_text],
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
    else:
        response = client.models.generate_content(
            model=model,
            contents=[part, prompt_text],
            config=types.GenerateContentConfig(**cfg_kwargs),
        )

    raw_output = _extract_visible_text(response)
    usage_info = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage_info = {
            "total_input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
            "total_output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
            "total_thought_tokens": getattr(response.usage_metadata, "thoughts_token_count", 0) or 0,
            "total_tool_use_tokens": getattr(response.usage_metadata, "tool_use_prompt_token_count", 0) or 0,
            "total_tokens": getattr(response.usage_metadata, "total_token_count", 0) or 0,
        }
    duration = time.time() - t0
    print(f"    ✓ {tag}Completed in {duration:.1f}s (Tokens: {usage_info.get('total_tokens', 0):,})")
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
    ensure_ffmpeg_in_path()
    parser = argparse.ArgumentParser(
        description="AI Multimodal Video to EDL Decision Generator (Vertex AI ADC + GCS + Silence-Aware Segmentation).",
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
    parser.add_argument("--model", default="gemini-4-hydrogen",
                        help="Vertex AI Gemini model name (default: gemini-4-hydrogen)")
    parser.add_argument("--processing", choices=["standard", "agentic"], default="standard",
                        help="Video processing mode: 'standard' (default, Non-Agentic Low-Res + silence-aware segmentation) or 'agentic'")
    parser.add_argument("--chunk-min-dur", type=float, default=1800.0,
                        help="Minimum chapter segment duration in seconds for silence-aware splitting (default: 1800 = 30 mins)")
    parser.add_argument("--chunk-max-dur", type=float, default=2400.0,
                        help="Maximum chapter segment duration in seconds before splitting at silence points (default: 2400 = 40 mins)")
    parser.add_argument("--timeout", type=int, default=600,
                        help="HTTP timeout in seconds for Vertex AI video inference (default: 600)")
    parser.add_argument("--max-output-tokens", type=int, default=65536,
                        help="Maximum output tokens for EDL generation (default: 65536)")
    parser.add_argument("--thinking-budget", type=int, default=None,
                        help="Thinking token budget mapped to thinking_level on Gemini 4 models (default: auto-calculated from video duration)")
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
                        help="Halt execution with exit code 1 when EDL validation finds ERROR issues")
    parser.add_argument("--lang", default="en",
                        help="Language for the EDL validation report (default: en)")
    parser.add_argument("--edl-max-gap-sec", type=float, default=0.05,
                        help="Maximum allowed gap in seconds between consecutive EDL cuts (default: 0.05)")
    parser.add_argument("--edl-known-cameras", default=None,
                        help=r"Comma-separated list of expected camera names, e.g. CAM1,CAM2 (default: ^CAM\d+$)")

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
    print(f"  • Architecture   : {'Standard Low-Res Multimodal + Silence-Aware Segmentation (30-40 min)' if args.processing == 'standard' else 'Agentic Video Understanding'}")
    print(f"  • Active Backend : Google Cloud Vertex AI (Project: {gcp_cfg['project']}, Location: {gcp_cfg['location']})")
    print(f"  • GCS Storage    : gs://{gcp_cfg['bucket']}/raw/ (Region: {gcp_cfg['region']}, 2-day Lifecycle)")
    print(f"  • Target CSV     : {edl_csv_path}")
    print(f"  • Target Report  : {report_path}")
    print("-" * 78)

    base_prompt_text = load_prompt_template(args.template)
    genai_client = get_vertex_client(
        project=gcp_cfg["project"],
        location=gcp_cfg["location"],
        timeout_ms=int(args.timeout * 1000),
    )

    local_video_path = None
    video_uri = None
    if is_gcs_uri:
        video_uri = args.video
    elif is_gdrive:
        video_uri, local_video_path = transfer_gdrive_to_gcs_with_cache(
            args.video,
            bucket_name=gcp_cfg["bucket"],
            gcs_prefix="raw",
            local_cache_dir=os.path.join(out_dir, "gdrive_inputs"),
            project=gcp_cfg["project"],
            region=gcp_cfg["region"],
            force=args.force_upload,
        )
    else:
        local_video_path = ensure_agentic_ready_video(args.video)

    total_video_dur = _probe_video_duration_sec(local_video_path) if local_video_path else 1800.0
    use_silence_segmentation = (
        local_video_path is not None
        and os.path.isfile(local_video_path)
        and args.processing == "standard"
        and total_video_dur > args.chunk_max_dur
    )

    chunks_dir = os.path.join(out_dir, "_edl_chunks")
    temp_gcs_uris = []

    try:
        if use_silence_segmentation:
            print(
                f"\n[Stage 2 - Step 1/3] 🔇 Video duration is {format_edl_timestamp(total_video_dur)} "
                f"(> {args.chunk_max_dur / 60.0:.0f} mins). Scanning for natural silence split points...",
                flush=True,
            )
            split_points = find_natural_split_points(
                local_video_path,
                start_sec=0.0,
                end_sec=total_video_dur,
                min_dur_sec=args.chunk_min_dur,
                max_dur_sec=args.chunk_max_dur,
            )
            if os.path.exists(chunks_dir):
                shutil.rmtree(chunks_dir, ignore_errors=True)

            chunks = slice_video_into_temp_chunks(local_video_path, split_points, chunks_dir)
            print(f"  ✓ Sliced {len(chunks)} segments at natural silence points into {chunks_dir}:", flush=True)
            for c in chunks:
                print(
                    f"    • Part {c['part_index']}/{c['total_parts']}: "
                    f"{format_edl_timestamp(c['start_sec'])} -> {format_edl_timestamp(c['end_sec'])} "
                    f"({c['duration_sec'] / 60.0:.1f} mins)",
                    flush=True,
                )

            print(f"\n[Stage 2 - Step 2/3] 🚀 Uploading & running parallel Non-Agentic inference ({len(chunks)} parts)...", flush=True)
            t_all_start = time.time()

            def _process_single_chunk(chunk_info):
                p_idx = chunk_info["part_index"]
                p_tot = chunk_info["total_parts"]
                c_path = chunk_info["path"]
                c_uri = upload_file_to_gcs_with_cache(
                    c_path,
                    bucket_name=gcp_cfg["bucket"],
                    gcs_prefix="raw/edl_chunks",
                    project=gcp_cfg["project"],
                    region=gcp_cfg["region"],
                    force_upload=True,
                )
                temp_gcs_uris.append(c_uri)

                c_prompt = _build_chunk_prompt(
                    base_prompt_text,
                    part_index=p_idx,
                    total_parts=p_tot,
                    start_sec=chunk_info["start_sec"],
                    end_sec=chunk_info["end_sec"],
                )
                c_client = get_vertex_client(
                    project=gcp_cfg["project"],
                    location=gcp_cfg["location"],
                    timeout_ms=int(args.timeout * 1000),
                )
                c_text, c_usage, c_dur = generate_edl_content_standard(
                    c_uri,
                    c_prompt,
                    client=c_client,
                    model=args.model,
                    max_output_tokens=args.max_output_tokens,
                    thinking_budget=args.thinking_budget,
                    video_duration_sec=chunk_info["duration_sec"],
                    label_prefix=f"Part {p_idx}/{p_tot}",
                    use_ticker=False,
                )
                c_rows, c_report = parse_edl_csv_and_report(c_text)
                return {
                    "part_index": p_idx,
                    "start_sec": chunk_info["start_sec"],
                    "end_sec": chunk_info["end_sec"],
                    "csv_rows": c_rows,
                    "report_md": c_report,
                    "usage": c_usage,
                    "duration": c_dur,
                }

            chunk_results = []
            with LiveTicker(f"Stage 2 Step 2/3: Vertex AI Gemini ({args.model}) analyzing {len(chunks)} silence-aligned parts in parallel"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(chunks), 3)) as executor:
                    futures = [executor.submit(_process_single_chunk, c) for c in chunks]
                    for fut in concurrent.futures.as_completed(futures):
                        r_done = fut.result()
                        chunk_results.append(r_done)
                        print(
                            f"    ✓ Part {r_done['part_index']}/{len(chunks)} completed in {r_done['duration']:.1f}s "
                            f"({len(r_done['csv_rows']) - 1 if r_done['csv_rows'] else 0} cuts)",
                            flush=True,
                        )

            chunk_results.sort(key=lambda x: x["part_index"])
            duration = time.time() - t_all_start
            usage_info = {
                "total_input_tokens": sum(r["usage"].get("total_input_tokens", 0) for r in chunk_results),
                "total_output_tokens": sum(r["usage"].get("total_output_tokens", 0) for r in chunk_results),
                "total_thought_tokens": sum(r["usage"].get("total_thought_tokens", 0) for r in chunk_results),
                "total_tool_use_tokens": sum(r["usage"].get("total_tool_use_tokens", 0) for r in chunk_results),
                "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in chunk_results),
            }
            csv_rows = shift_and_merge_chunk_edl_rows(chunk_results)
            report_sections = [
                f"## Part {r['part_index']}/{len(chunk_results)} "
                f"(`{format_edl_timestamp(r['start_sec'])}` - `{format_edl_timestamp(r['end_sec'])}`)\n\n{r['report_md']}"
                for r in chunk_results
            ]
            report_md = "\n\n---\n\n".join(report_sections)
            response_text = report_md

        else:
            if not video_uri:
                video_uri = upload_file_to_gcs_with_cache(
                    local_video_path,
                    bucket_name=gcp_cfg["bucket"],
                    gcs_prefix="raw",
                    project=gcp_cfg["project"],
                    region=gcp_cfg["region"],
                    force_upload=args.force_upload,
                )

            prompt_text = _build_chunk_prompt(
                base_prompt_text,
                part_index=1,
                total_parts=1,
                start_sec=0.0,
                end_sec=total_video_dur,
            )

            if args.processing == "agentic":
                try:
                    response_text, usage_info, duration = call_agentic_video_edl(
                        video_uri,
                        prompt_text,
                        client=genai_client,
                        model=args.model,
                        max_output_tokens=args.max_output_tokens,
                        thinking_budget=(
                            args.thinking_budget
                            if args.thinking_budget is not None
                            else calculate_dynamic_thinking_budget(total_video_dur)
                        ),
                        timeout_sec=float(args.timeout),
                    )
                except Exception as e:
                    print(
                        f"\n[Warning] Agentic processing encountered ({e}). Falling back to Vertex AI Standard Low-Res generateContent...",
                        file=sys.stderr,
                    )
                    response_text, usage_info, duration = generate_edl_content_standard(
                        video_uri,
                        prompt_text,
                        client=genai_client,
                        model=args.model,
                        max_output_tokens=args.max_output_tokens,
                        thinking_budget=args.thinking_budget,
                        video_duration_sec=total_video_dur,
                    )
            else:
                print(f"\n[Stage 2 - Step 2/3] 🤖 Calling Vertex AI Gemini model: {args.model} (Standard Low-Res Mode) ...", flush=True)
                response_text, usage_info, duration = generate_edl_content_standard(
                    video_uri,
                    prompt_text,
                    client=genai_client,
                    model=args.model,
                    max_output_tokens=args.max_output_tokens,
                    thinking_budget=args.thinking_budget,
                    video_duration_sec=total_video_dur,
                )

            csv_rows, report_md = parse_edl_csv_and_report(response_text)

        if not csv_rows or len(csv_rows) <= 1:
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
        print(f"\n{val_report_str}", flush=True)

        # Write CSV
        with open(edl_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(csv_rows)
        print("\n[Stage 2 - Step 3/3] 📁 Saved outputs:", flush=True)
        print(f"  ✓ EDL Decision CSV : {edl_csv_path} ({len(csv_rows) - 1} shot cuts)", flush=True)

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
            if usage_info.get("total_tool_use_tokens"):
                token_section += f"- **Tool Use Tokens**: `{usage_info.get('total_tool_use_tokens', 0):,}`\n"
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
        if os.path.exists(chunks_dir):
            shutil.rmtree(chunks_dir, ignore_errors=True)
            print(f"  🗑️  Cleaned up temporary local chunk directory: {chunks_dir}")
        for c_uri in temp_gcs_uris:
            delete_gcs_blob(c_uri, project=gcp_cfg.get("project"))
        if args.cleanup_gcs and video_uri:
            delete_gcs_blob(video_uri, project=gcp_cfg.get("project"))

    print("\n" + "=" * 78)
    print("✅  AI EDL Generation Completed Successfully!")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
