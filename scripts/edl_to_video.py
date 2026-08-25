#!/usr/bin/env python3
"""
EDL to Video Rendering CLI Tool (edl_to_video.py).
Converts Edit Decision Lists (EDL in CSV format, e.g. 1.csv) into final edited multi-camera videos.

Supported CSV Columns (Case-insensitive):
  - Start Time : Start_Time, Start, In, In_Point, From
  - End Time   : End_Time, End, Out, Out_Point, To
  - Camera     : Best_Camera, Camera, Cam, Source, Source_File, Clip, Angle
  - Reason/Rule: 剪輯規則, 剪輯原因, Rules, Reasons, Notes, Description

Usage Examples:
  # Example 1: Basic CSV EDL Rendering with Camera Mapping
  python3 scripts/edl_to_video.py \
    --edl part1/1.csv \
    --media-dir ./part1/ \
    --camera-map "CAM1=C6036_1080P_part1.mp4,CAM2=C6051_1080P_part1.mp4" \
    --output ./part1/final_edit.mp4

  # Example 2: Auto-detect Media Files in Directory
  python3 scripts/edl_to_video.py \
    --edl 1.csv \
    --media-dir ./raw_footage/ \
    --output ./final_cut.mp4
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def parse_time_to_seconds(t_val):
    """
    Parse time strings (MM:SS.mmm, HH:MM:SS.mmm, or float seconds) to float seconds.
    Examples:
      - 00:38.500 -> 38.5
      - 02:21.000 -> 141.0
      - 01:04:27.371 -> 3867.371
    """
    if t_val is None:
        return 0.0
    if isinstance(t_val, (int, float)):
        return float(t_val)
    t_str = str(t_val).strip().replace('"', '').replace("'", "")
    if not t_str:
        return 0.0
    try:
        return float(t_str)
    except ValueError:
        pass

    parts = t_str.split(":")
    try:
        if len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m = float(parts[0])
            s = float(parts[1])
            return m * 60 + s
        elif len(parts) == 1:
            return float(parts[0])
    except ValueError:
        raise ValueError(f"Unable to parse time format: '{t_val}'")
    raise ValueError(f"Unsupported time string: '{t_val}'")


def format_seconds(sec):
    """
    Format float seconds into HH:MM:SS.mmm format.
    """
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def parse_camera_map(map_str):
    """
    Parse camera mapping string: "CAM1=path/to/c1.mp4,CAM2=path/to/c2.mp4" or JSON string.
    """
    if not map_str:
        return {}
    map_str = map_str.strip()
    if map_str.startswith("{") and map_str.endswith("}"):
        try:
            return json.loads(map_str)
        except json.JSONDecodeError:
            pass

    mapping = {}
    items = [item.strip() for item in map_str.split(",") if item.strip()]
    for item in items:
        if "=" in item:
            k, v = item.split("=", 1)
            mapping[k.strip().upper()] = v.strip()
            mapping[k.strip()] = v.strip()
        elif ":" in item:
            k, v = item.split(":", 1)
            mapping[k.strip().upper()] = v.strip()
            mapping[k.strip()] = v.strip()
    return mapping


def load_edl_csv(csv_path):
    """
    Parse EDL CSV file (e.g. 1.csv).
    Returns list of cut segment dictionaries:
      [
        {
          "index": 1,
          "start_sec": 38.5,
          "end_sec": 141.0,
          "camera": "CAM1",
          "rule": "[強制] 主要發話者鎖定",
          "reason": "主持人正式開場引言並介紹來賓背景與提問"
        }, ...
      ]
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"EDL CSV file not found: {csv_path}")

    segments = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        # Detect delimiter (comma or tab)
        sample = f.read(4096)
        f.seek(0)
        delimiter = "	" if "	" in sample and "," not in sample else ","
        reader = csv.reader(f, delimiter=delimiter)
        raw_rows = [row for row in reader if row and any(field.strip() for field in row)]

    if not raw_rows:
        return []

    # Map column indices from header
    header = [h.strip().lower() for h in raw_rows[0]]
    start_col = -1
    end_col = -1
    cam_col = -1
    rule_col = -1
    reason_col = -1

    for i, col_name in enumerate(header):
        if col_name in ("start_time", "start", "in", "start_sec", "in_point", "from"):
            start_col = i
        elif col_name in ("end_time", "end", "out", "end_sec", "out_point", "to"):
            end_col = i
        elif col_name in ("best_camera", "camera", "cam", "source", "source_file", "clip", "angle", "source_camera"):
            cam_col = i
        elif col_name in ("剪輯規則", "rule", "rules", "rule_type"):
            rule_col = i
        elif col_name in ("剪輯原因", "reason", "reasons", "notes", "description", "label", "comment"):
            reason_col = i

    has_header = (start_col != -1 and end_col != -1) or (start_col != -1 and cam_col != -1)
    data_rows = raw_rows[1:] if has_header else raw_rows

    if not has_header:
        # Default column ordering fallback: [Start_Time, End_Time, Best_Camera, ...]
        start_col = 0
        end_col = 1
        cam_col = 2
        rule_col = 3 if len(raw_rows[0]) > 3 else -1
        reason_col = 4 if len(raw_rows[0]) > 4 else -1

    for idx, row in enumerate(data_rows, start=1):
        if not row:
            continue
        try:
            start_raw = row[start_col] if start_col != -1 and start_col < len(row) else "0"
            end_raw = row[end_col] if end_col != -1 and end_col < len(row) else None
            cam_raw = row[cam_col] if cam_col != -1 and cam_col < len(row) else ""
            rule_raw = row[rule_col] if rule_col != -1 and rule_col < len(row) else ""
            reason_raw = row[reason_col] if reason_col != -1 and reason_col < len(row) else ""

            start_s = parse_time_to_seconds(start_raw)
            end_s = parse_time_to_seconds(end_raw) if end_raw else None

            segments.append({
                "index": idx,
                "start_sec": start_s,
                "end_sec": end_s,
                "camera": cam_raw.strip(),
                "rule": rule_raw.strip(),
                "reason": reason_raw.strip()
            })
        except Exception as e:
            print(f"[Warning] Skipping unparseable CSV row #{idx}: {row} ({e})", file=sys.stderr)

    return segments


def auto_discover_camera_files(media_dir, part_tag=None):
    """
    Auto-discover camera video files in media_dir with Part-awareness.
    If part_tag is provided (e.g., 'part1'), prioritizes matching '*_part1.mp4' footage.
    Maps CAM1 -> 1st camera, CAM2 -> 2nd camera, etc.
    """
    if not media_dir or not os.path.exists(media_dir):
        return {}

    mapping = {}
    all_files = sorted(os.listdir(media_dir))
    candidates = []

    # 1. First attempt: filter by part_tag if specified
    if part_tag:
        pt_lower = part_tag.lower()
        for f in all_files:
            if any(f.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m4v")):
                if "merged" not in f.lower() and "final" not in f.lower() and "seg_" not in f.lower():
                    if pt_lower in f.lower():
                        candidates.append(f)

    # 2. Fallback: all non-intermediate video files
    if not candidates:
        for f in all_files:
            if any(f.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m4v")):
                if "merged" not in f.lower() and "final" not in f.lower() and "seg_" not in f.lower():
                    candidates.append(f)

    # Map CAM1, CAM2 ...
    for idx, fname in enumerate(candidates, start=1):
        mapping[f"CAM{idx}"] = fname
        mapping[f"cam{idx}"] = fname
        mapping[f"CAM_{idx}"] = fname
        mapping[f"C{idx}"] = fname

    return mapping


def resolve_media_file(camera_identifier, media_dir=None, camera_map=None, part_tag=None):
    """
    Resolve camera string (e.g. CAM1, CAM2, or filename) to an actual video file path.
    """
    camera_map = camera_map or {}
    cam_str = camera_identifier.strip()
    cam_upper = cam_str.upper()
    cam_lower = cam_str.lower()

    # 1. Match from provided camera_map
    effective_name = cam_str
    if cam_str in camera_map:
        effective_name = camera_map[cam_str]
    elif cam_upper in camera_map:
        effective_name = camera_map[cam_upper]
    elif cam_lower in camera_map:
        effective_name = camera_map[cam_lower]

    eff_lower = effective_name.lower()

    # 2. Direct absolute or relative path check
    if os.path.exists(effective_name):
        return os.path.abspath(effective_name)

    # 3. Search within media_dir
    if media_dir and os.path.exists(media_dir):
        # Exact filename
        candidate = os.path.join(media_dir, effective_name)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)

        # Check with video extensions
        for ext in (".mp4", ".mov", ".mkv", ".m4v", ".MP4", ".MOV"):
            candidate = os.path.join(media_dir, f"{effective_name}{ext}")
            if os.path.exists(candidate):
                return os.path.abspath(candidate)

        # Auto-discovery with Part awareness
        discovered = auto_discover_camera_files(media_dir, part_tag=part_tag)
        if cam_upper in discovered:
            target = os.path.join(media_dir, discovered[cam_upper])
            if os.path.exists(target):
                return os.path.abspath(target)

    raise FileNotFoundError(f"Could not resolve video file for camera '{camera_identifier}' (Mapped: '{effective_name}', Search Dir: {media_dir})")


def cut_segment_stream_copy(input_path, output_path, start_sec, end_sec):
    """
    Extract a segment using fast lossless stream copying.
    """
    dur_sec = end_sec - start_sec if end_sec is not None else None
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    cmd.extend(["-ss", format_seconds(start_sec), "-i", input_path])
    if dur_sec is not None and dur_sec > 0:
        cmd.extend(["-t", format_seconds(dur_sec)])
    elif end_sec is not None:
        cmd.extend(["-to", format_seconds(end_sec)])

    cmd.extend(["-c:v", "copy", "-c:a", "copy", output_path])
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Stream-copy cut failed: {res.stderr}")
    return output_path


def cut_segment_reencode(input_path, output_path, start_sec, end_sec,
                         encoder="h264_videotoolbox", video_bitrate="8000k", audio_bitrate="192k"):
    """
    Extract a segment using frame-accurate hardware-accelerated re-encoding.
    Matches reference format: ffmpeg -nostdin -i input -ss start -to end -c:v h264_videotoolbox -b:v 8000k -c:a aac output
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path,
        "-ss", format_seconds(start_sec),
        "-to", format_seconds(end_sec),
        "-c:v", encoder,
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        if encoder != "libx264":
            cmd[cmd.index(encoder)] = "libx264"
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Re-encode cut failed: {res.stderr}")
    return output_path


def concatenate_segments(segment_paths, output_path, concat_list_path=None):
    """
    Concatenate video segments using FFmpeg concat demuxer.
    """
    if not segment_paths:
        raise ValueError("No segments provided for concatenation.")

    list_path = concat_list_path or os.path.join(os.path.dirname(output_path), "concat_manifest.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in segment_paths:
            abs_p = os.path.abspath(p)
            f.write(f"file '{abs_p}'\n")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg concat demuxer failed: {res.stderr}")

    if os.path.exists(list_path) and not concat_list_path:
        os.remove(list_path)
    return output_path


def render_edl_to_video(edl_path, output_path=None, media_dir=None, camera_map=None,
                        re_encode=True, encoder="h264_videotoolbox",
                        video_bitrate="8000k", audio_bitrate="192k",
                        workers=4, keep_temp=False, temp_dir=None):
    """
    Main pipeline to render an EDL CSV file into a final cut video.
    Default: Frame-accurate hardware-accelerated re-encoding with h264_videotoolbox.
    """
    t0 = time.time()
    segments = load_edl_csv(edl_path)
    if not segments:
        raise ValueError(f"No valid segments found in EDL file: {edl_path}")

    # Extract part tag from EDL filename (e.g. edl_part1.csv -> part1)
    edl_basename = os.path.basename(edl_path)
    edl_dir = os.path.dirname(os.path.abspath(edl_path))
    part_match = re.search(r"(part\d+)", edl_basename, re.IGNORECASE)
    part_tag = part_match.group(1).lower() if part_match else None

    # Derive output_path if not provided (e.g. edl_part1.csv -> final_cut_part1.mp4)
    if not output_path:
        out_suffix = f"_{part_tag}" if part_tag else ""
        output_path = os.path.join(edl_dir, f"final_cut{out_suffix}.mp4")

    # Media directory defaults to EDL directory if not provided
    media_dir = media_dir or edl_dir

    total_segments = len(segments)
    cam_mapping = parse_camera_map(camera_map)

    # Auto-discover cameras from media_dir with part awareness if not specified
    if not cam_mapping and media_dir:
        cam_mapping = auto_discover_camera_files(media_dir, part_tag=part_tag)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    custom_temp = temp_dir is not None
    work_temp_dir = temp_dir or tempfile.mkdtemp(prefix="edl_render_")
    os.makedirs(work_temp_dir, exist_ok=True)

    print("\n" + "=" * 78)
    print(f"🎬  EDL to Video Renderer: Assembling {total_segments} Cuts from CSV")
    print("=" * 78)
    print(f"  • EDL CSV File : {edl_path}")
    print(f"  • Output Target: {output_path}")
    print(f"  • Media Dir    : {media_dir}")
    print(f"  • Part Tag     : {part_tag or 'N/A'}")
    print(f"  • Cutting Mode : {'Frame-Accurate Re-encode (' + encoder + ')' if re_encode else 'Lossless Stream Copy (-c copy)'}")
    print(f"  • Concurrency  : {workers} workers")
    print("-" * 78)

    resolved_tasks = []
    total_duration = 0.0

    for seg in segments:
        idx = seg["index"]
        start_s = seg["start_sec"]
        end_s = seg["end_sec"]
        cam_id = seg["camera"]
        rule = seg.get("rule", "")
        reason = seg.get("reason", "")

        if end_s is not None:
            seg_dur = max(0.0, end_s - start_s)
            total_duration += seg_dur
        else:
            seg_dur = 0.0

        src_file = resolve_media_file(cam_id, media_dir=media_dir, camera_map=cam_mapping, part_tag=part_tag)
        seg_out = os.path.join(work_temp_dir, f"seg_{idx:04d}.mp4")

        resolved_tasks.append({
            "index": idx,
            "src": src_file,
            "output": seg_out,
            "start": start_s,
            "end": end_s,
            "camera": cam_id,
            "src_basename": os.path.basename(src_file),
            "rule": rule,
            "reason": reason,
            "dur": seg_dur
        })

    print(f"  ℹ️  Estimated Total Output Duration: {format_seconds(total_duration)} ({total_duration:.2f}s)\n")

    # Step 1: Extract all segments in parallel
    print(f"[Step 1/2] ✂️  Extracting {total_segments} video segments...")
    seg_outputs = [None] * total_segments

    def _process_single_segment(task):
        t_start = time.time()
        idx = task["index"]
        src = task["src"]
        dst = task["output"]
        s = task["start"]
        e = task["end"]
        cam = task["camera"]
        rule_desc = f" ({task['rule']})" if task.get("rule") else ""

        if re_encode:
            cut_segment_reencode(
                src, dst, s, e, encoder=encoder,
                video_bitrate=video_bitrate, audio_bitrate=audio_bitrate
            )
        else:
            try:
                cut_segment_stream_copy(src, dst, s, e)
            except Exception as stream_err:
                print(f"    [Fallback] Segment #{idx} stream-copy failed ({stream_err}), falling back to re-encode...")
                cut_segment_reencode(
                    src, dst, s, e, encoder=encoder,
                    video_bitrate=video_bitrate, audio_bitrate=audio_bitrate
                )

        elapsed = time.time() - t_start
        return idx, dst, cam, s, e, elapsed, rule_desc

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_single_segment, task): task for task in resolved_tasks}
        completed_count = 0
        for future in as_completed(futures):
            idx, dst, cam, s, e, elapsed, rule_desc = future.result()
            seg_outputs[idx - 1] = dst
            completed_count += 1
            progress_pct = (completed_count / total_segments) * 100
            print(f"  [{completed_count:02d}/{total_segments:02d} ({progress_pct:4.0f}%)] Cut #{idx:02d} | {cam} ({format_seconds(s)} → {format_seconds(e)}) [{elapsed:.2f}s]{rule_desc}")

    # Step 2: Concatenate all segments
    print(f"\n[Step 2/2] 🔗 Concatenating all {total_segments} segments into final edited video...")
    concat_list_file = os.path.join(work_temp_dir, "concat_manifest.txt")
    concatenate_segments(seg_outputs, output_path, concat_list_path=concat_list_file)

    total_time = time.time() - t0
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

    # Cleanup temporary segment files
    if not keep_temp and not custom_temp:
        shutil.rmtree(work_temp_dir, ignore_errors=True)

    print("\n" + "=" * 78)
    print("✅  EDL Video Assembly Completed Successfully!")
    print(f"  • Output File  : {output_path} ({file_size_mb:.1f} MB)")
    print(f"  • Total Cuts   : {total_segments} segments")
    print(f"  • Total Time   : {total_time:.2f}s (Average {total_time / total_segments:.2f}s per cut)")
    print("=" * 78 + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="EDL CSV to Video Renderer: Convert an Edit Decision List (CSV format like 1.csv) into a final edited video.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--edl", required=True, help="Path to input EDL CSV file (e.g. edl_part1.csv)")
    parser.add_argument("-o", "--output", default=None, help="Path to output video file (default: auto-derived final_cut_partX.mp4)")
    parser.add_argument("--media-dir", default=None, help="Directory containing source camera video files")
    parser.add_argument("--camera-map", default=None, help="Camera name to file mapping (e.g. CAM1=CAM1_part1.mp4,CAM2=CAM2_part1.mp4)")

    # Encoding options
    parser.add_argument("--stream-copy", action="store_true", help="Force raw stream-copy without re-encoding (may cause non-keyframe glitches)")
    parser.add_argument("--encoder", default="h264_videotoolbox", help="Video encoder for frame-accurate re-encoding (default: h264_videotoolbox, fallback: libx264)")
    parser.add_argument("--video-bitrate", default="8000k", help="Video bitrate for re-encoding (default: 8000k)")
    parser.add_argument("--audio-bitrate", default="192k", help="Audio bitrate for re-encoding (default: 192k)")

    # Performance
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker threads for segment extraction (default: 4)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary segment files for inspection")
    parser.add_argument("--temp-dir", default=None, help="Custom temporary directory for segments")

    args = parser.parse_args()

    try:
        render_edl_to_video(
            edl_path=args.edl,
            output_path=args.output,
            media_dir=args.media_dir,
            camera_map=args.camera_map,
            re_encode=not args.stream_copy,
            encoder=args.encoder,
            video_bitrate=args.video_bitrate,
            audio_bitrate=args.audio_bitrate,
            workers=args.workers,
            keep_temp=args.keep_temp,
            temp_dir=args.temp_dir
        )
    except Exception as e:
        print(f"\n[Error] EDL rendering failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
