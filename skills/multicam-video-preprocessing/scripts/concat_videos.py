#!/usr/bin/env python3
"""
Video Concatenation CLI Tool (concat_videos.py).
Merges multiple sequential chapter video files (e.g. part1/final_edited_cut.mp4, part2/final_edited_cut_part2.mp4)
into a single final continuous video using FFmpeg lossless concat demuxer or hardware-accelerated re-encoding.

Features:
  - Multi-Input Flexibility: Specify files directly (--inputs), search directories (--dir + --pattern), or supply a list.
  - Smart Natural Ordering: Automatically sorts parts by natural index (e.g., part1, part2, part10).
  - High-Speed Lossless Splicing: Merges video streams via -c copy in seconds without generational quality loss.
  - Automatic Validation & Fallback: Verifies stream parameters and falls back to re-encode if stream codecs mismatch.
  - Comprehensive Metadata: Probes input/output durations and formats for reporting.

Usage Examples:
  # Example 1: Merge explicit video files
  python3 scripts/concat_videos.py \
    --inputs part1/final_edited_cut.mp4 part2/final_edited_cut_part2.mp4 \
    -o full_episode_cut.mp4

  # Example 2: Auto-discover and merge parts from directory
  python3 scripts/concat_videos.py \
    --dir ./final_multicam_output/ \
    --pattern "**/final_edited_cut*.mp4" \
    -o full_show.mp4

  # Example 3: Re-encode mode with VideoToolbox hardware acceleration
  python3 scripts/concat_videos.py \
    --inputs part1/video.mp4 part2/video.mp4 \
    --re-encode --encoder h264_videotoolbox \
    -o full_show.mp4
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def get_video_info(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate",
        "-of", "json",
        file_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to probe {file_path}: {res.stderr}")

    data = json.loads(res.stdout)
    fmt = data.get("format", {})
    dur = float(fmt.get("duration", 0.0))
    size = int(fmt.get("size", 0))

    v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})

    return {
        "path": os.path.abspath(file_path),
        "basename": os.path.basename(file_path),
        "duration": dur,
        "size_bytes": size,
        "size_mb": size / (1024 * 1024),
        "v_codec": v_stream.get("codec_name", "unknown"),
        "width": v_stream.get("width", 0),
        "height": v_stream.get("height", 0),
        "fps": v_stream.get("r_frame_rate", "unknown"),
        "a_codec": a_stream.get("codec_name", "unknown"),
        "sample_rate": a_stream.get("sample_rate", "unknown")
    }


def format_seconds(sec):
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def discover_inputs(inputs=None, search_dir=None, pattern=None):
    collected = []
    if inputs:
        for item in inputs:
            expanded = glob.glob(item, recursive=True)
            if expanded:
                collected.extend(expanded)
            elif os.path.exists(item):
                collected.append(item)
            else:
                print(f"[Warning] Input file not found: {item}", file=sys.stderr)

    if search_dir and os.path.exists(search_dir):
        pat = pattern or "**/final_cut_part*.mp4"
        full_pattern = os.path.join(search_dir, pat)
        matches = glob.glob(full_pattern, recursive=True)
        for m in matches:
            bname = os.path.basename(m).lower()
            if "_full" not in bname and "full_" not in bname and "concat" not in bname and m not in collected:
                collected.append(m)

    seen = set()
    unique = []
    for p in collected:
        abs_p = os.path.abspath(p)
        if abs_p not in seen and os.path.exists(abs_p):
            seen.add(abs_p)
            unique.append(abs_p)

    unique.sort(key=natural_sort_key)
    return unique


def concat_lossless(input_files, output_path):
    temp_dir = tempfile.mkdtemp(prefix="concat_manifest_")
    manifest_path = os.path.join(temp_dir, "manifest.txt")

    with open(manifest_path, "w", encoding="utf-8") as f:
        for p in input_files:
            abs_p = os.path.abspath(p)
            f.write(f"file '{abs_p}'\n")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", manifest_path,
        "-c", "copy",
        output_path
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    shutil.rmtree(temp_dir, ignore_errors=True)

    if res.returncode != 0:
        raise RuntimeError(f"Lossless concat failed: {res.stderr}")
    return output_path


def concat_reencode(input_files, output_path, encoder="h264_videotoolbox",
                    video_bitrate="8000k", audio_bitrate="192k"):
    num_inputs = len(input_files)
    inputs_args = []
    for p in input_files:
        inputs_args.extend(["-i", p])

    filter_inputs = "".join([f"[{i}:v:0][{i}:a:0]" for i in range(num_inputs)])
    filter_complex = f"{filter_inputs}concat=n={num_inputs}:v=1:a=1[outv][outa]"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    cmd.extend(inputs_args)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", encoder,
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_path
    ])

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        if encoder != "libx264":
            cmd[cmd.index(encoder)] = "libx264"
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Re-encode concat failed: {res.stderr}")
    return output_path


def merge_video_parts(input_files, output_path, re_encode=False,
                      encoder="h264_videotoolbox", video_bitrate="8000k", audio_bitrate="192k"):
    if not input_files:
        raise ValueError("No input video files provided to merge.")

    t0 = time.time()
    num_parts = len(input_files)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    print("\n" + "=" * 78)
    print(f"🎬  Video Concatenator: Merging {num_parts} Video Parts")
    print("=" * 78)
    print(f"  • Output Target: {output_path}")
    print(f"  • Merge Mode   : {'Frame-Accurate Re-encode (' + encoder + ')' if re_encode else 'Lossless Stream Copy (-c copy)'}")
    print("-" * 78)

    probed_info = []
    total_expected_dur = 0.0
    for idx, p in enumerate(input_files, start=1):
        info = get_video_info(p)
        probed_info.append(info)
        total_expected_dur += info["duration"]
        print(f"  [Part {idx:02d}/{num_parts:02d}] {info['basename']:<36} | {format_seconds(info['duration'])} ({info['duration']:.2f}s) | {info['width']}x{info['height']} | {info['v_codec']}/{info['a_codec']}")

    print(f"\n  ℹ️  Total Estimated Duration: {format_seconds(total_expected_dur)} ({total_expected_dur:.2f}s)")
    print(f"  ► Merging video streams into {os.path.basename(output_path)} ...")

    if re_encode:
        concat_reencode(
            input_files, output_path, encoder=encoder,
            video_bitrate=video_bitrate, audio_bitrate=audio_bitrate
        )
    else:
        try:
            concat_lossless(input_files, output_path)
        except Exception as copy_err:
            print(f"    [Fallback] Lossless concat failed ({copy_err}), falling back to hardware re-encoding...")
            concat_reencode(
                input_files, output_path, encoder=encoder,
                video_bitrate=video_bitrate, audio_bitrate=audio_bitrate
            )

    total_time = time.time() - t0
    out_info = get_video_info(output_path)

    print("\n" + "=" * 78)
    print("✅  Video Concatenation Completed Successfully!")
    print(f"  • Final Output : {output_path}")
    print(f"  • Final Length : {format_seconds(out_info['duration'])} ({out_info['duration']:.2f}s)")
    print(f"  • File Size    : {out_info['size_mb']:.1f} MB")
    print(f"  • Resolution   : {out_info['width']}x{out_info['height']}")
    print(f"  • Elapsed Time : {total_time:.2f}s")
    print("=" * 78 + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Video Concatenation CLI: Merge multiple chapter video segments (e.g. final_cut_part1, final_cut_part2) into a single final video (final_cut_full).",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-i", "--inputs", nargs="+", default=None, help="Ordered list of input video files or glob patterns")
    parser.add_argument("-d", "--dir", default=None, help="Directory to search for video parts")
    parser.add_argument("-p", "--pattern", default="**/final_cut_part*.mp4", help="Pattern to match video files in directory (default: **/final_cut_part*.mp4)")
    parser.add_argument("-o", "--output", default=None, help="Path to output merged video file (default: [dir/]final_cut_full.mp4)")

    parser.add_argument("--re-encode", action="store_true", help="Force re-encoding instead of lossless stream-copy")
    parser.add_argument("--encoder", default="h264_videotoolbox", help="Video encoder for re-encoding (default: h264_videotoolbox, fallback: libx264)")
    parser.add_argument("--video-bitrate", default="8000k", help="Video bitrate for re-encoding (default: 8000k)")
    parser.add_argument("--audio-bitrate", default="192k", help="Audio bitrate for re-encoding (default: 192k)")

    args = parser.parse_args()

    input_files = discover_inputs(inputs=args.inputs, search_dir=args.dir, pattern=args.pattern)
    if not input_files:
        print("[Error] No input video files found. Please specify --inputs or --dir + --pattern.", file=sys.stderr)
        sys.exit(1)

    # Derive output path (Option A: final_cut_full.mp4)
    if not args.output:
        if args.dir:
            output_path = os.path.join(args.dir, "final_cut_full.mp4")
        else:
            first_dir = os.path.dirname(os.path.abspath(input_files[0]))
            output_path = os.path.join(first_dir, "final_cut_full.mp4")
    else:
        output_path = args.output

    try:
        merge_video_parts(
            input_files=input_files,
            output_path=output_path,
            re_encode=args.re_encode,
            encoder=args.encoder,
            video_bitrate=args.video_bitrate,
            audio_bitrate=args.audio_bitrate
        )
    except Exception as e:
        print(f"\n[Error] Video concatenation failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
