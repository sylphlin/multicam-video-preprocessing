#!/usr/bin/env python3
"""
Multi-Camera Video Pipeline CLI Tool (Universal Pipeline Engine).
Supports 2 to 6 Cameras with Guaranteed Compact Canvas (Max <= 1920x1080, Min >= 640x480/CAM).
Modular Architecture:
  1. Global Audio Time Alignment (modules.audio_sync)
  2. Full-Length EBU R128 Audio Loudness Normalization (modules.audio_normalizer)
  3. Chapter Pause Segmentation & Stream-Copy Slicing (modules.video_segmenter)
  4. Full-Length Synchronized Camera Masters Export (for NLE editing)
  5. Multi-in-One Grid Video Composition for 2-6 Cameras (modules.video_composer)
  6. Reporting and Data Export (modules.reporter)
  7. Time Utility Conversions (modules.time_utils)

CLI Examples:
  # Example 1: Multi-Camera Time Alignment Analysis Only
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 CAM3.mp4

  # Example 2: Full Pipeline (Sync + EBU R128 + 30-40 min Chapter Slicing + Multi-in-One Merge + Synced Masters)
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --auto-split --split-min-dur 30 --split-max-dur 40 \
    --normalize --merge \
    --output-dir ./output/
"""

import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.time_utils import parse_time_to_seconds, format_seconds
from modules.audio_sync import sync_all_targets
from modules.audio_normalizer import normalize_all_audio_tracks
from modules.video_segmenter import (
    compute_common_overlap_range, find_natural_split_points, build_part_segments,
    cut_single_clip, cut_all_split_parts
)
from modules.video_composer import (
    compute_grid_spec, compose_multicam_video
)
from modules.reporter import (
    print_sync_table, print_split_summary,
    export_sync_json, export_sync_csv
)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Camera Video Preprocessing CLI: Global Time Sync + EBU R128 Loudness Normalization + Chapter Segmentation + 2-6 CAM Multi-in-One Composition",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    # Camera Inputs (Supports 2 to 6 Cameras)
    parser.add_argument("--ref", required=True, help="Reference anchor camera video path (CAM1)")
    parser.add_argument("--targets", "--target", nargs="+", required=True, help="One or more target camera video paths (CAM2, CAM3... up to CAM6)")

    # Mode 1: Manual Trim Range
    parser.add_argument("--ref-start", default=None, help="Reference camera manual start time (HH:MM:SS.mmm or seconds)")
    parser.add_argument("--ref-end", default=None, help="Reference camera manual end time (HH:MM:SS.mmm or seconds)")

    # Mode 2: Auto Chapter / Pause Segmentation
    parser.add_argument("--auto-split", action="store_true", help="Enable natural pause chapter segmentation (30-40 min target windows)")
    parser.add_argument("--split-min-dur", type=float, default=30.0, help="Minimum segment duration in minutes (default: 30.0)")
    parser.add_argument("--split-max-dur", type=float, default=40.0, help="Maximum segment duration in minutes (default: 40.0)")

    # Multi-in-One Merging (AI Model Token Optimization)
    parser.add_argument("--merge", "--multi-in-one", dest="merge", action="store_true", help="Render merged multi-in-one grid video (side-by-side/grid) to save tokens for AI models")
    parser.add_argument("--encoder", default="h264_videotoolbox", help="Video encoder for rendering (default: h264_videotoolbox, fallback: libx264)")

    # Output & Naming Controls
    parser.add_argument("--output-dir", default=None, help="Output directory for sub-clips and reports (default: current directory)")
    parser.add_argument("--suffix", default="_synced", help="Filename suffix for synchronized full/trimmed export (default: _synced)")
    parser.add_argument("--ref-output", default=None, help="Custom output filename for reference camera (optional)")
    parser.add_argument("--target-outputs", nargs="+", default=None, help="Custom output filenames for target cameras (optional)")
    parser.add_argument("--export-json", default=None, help="Path to export JSON report")
    parser.add_argument("--export-csv", default=None, help="Path to export CSV report")

    # Audio Normalization & Encoding
    parser.add_argument("--normalize", action="store_true", help="Enable EBU R128 (-14 LUFS) full-length audio normalization")
    parser.add_argument("--lufs", type=float, default=-14.0, help="Target integrated loudness in LUFS (default: -14.0)")
    parser.add_argument("--lra", type=float, default=11.0, help="Target loudness range in LU (default: 11.0)")
    parser.add_argument("--tp", type=float, default=-1.5, help="Maximum true peak limit in dBTP (default: -1.5)")
    parser.add_argument("--video-bitrate", default="6000k", help="Video bitrate for re-encoding (default: 6000k)")
    parser.add_argument("--audio-bitrate", default="192k", help="Audio bitrate for re-encoding (default: 192k)")

    # Performance Parameters
    parser.add_argument("--sr", type=int, default=8000, help="Audio sampling rate for FFT alignment in Hz (default: 8000)")
    parser.add_argument("--sample-dur", type=float, default=None, help="Limit sample duration in seconds for quick alignment test (default: full length)")
    parser.add_argument("--workers", type=int, default=2, help="Parallel worker threads (default: 2)")

    args = parser.parse_args()

    # Validate input files
    all_inputs = [args.ref] + args.targets
    for p in all_inputs:
        if not os.path.exists(p):
            print(f"[Error] File not found: {p}", file=sys.stderr)
            sys.exit(1)

    total_cams = len(all_inputs)
    if total_cams > 6:
        print(f"[Warning] Processing {total_cams} cameras (Optimized compact layouts are designed for 2 to 6 cameras).", file=sys.stderr)

    # Compute grid layout specifications (Max <= 1920x1080, Min >= 640x480)
    grid_spec = compute_grid_spec(total_cams)
    cols = grid_spec["cols"]
    rows = grid_spec["rows"]
    cw = grid_spec["cell_width"]
    ch = grid_spec["cell_height"]
    tot_w = grid_spec["total_width"]
    tot_h = grid_spec["total_height"]

    print("\n" + "=" * 78)
    print(f"🎬  Multi-Camera Preprocessing Pipeline ({total_cams} Cameras: {cols}x{rows} Grid, {cw}x{ch}/cell -> Total Canvas {tot_w}x{tot_h})")
    print("=" * 78)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    # ---------------------------------------------------------
    # Step 1: Global Audio FFT Time Alignment (Audio Sync)
    # ---------------------------------------------------------
    print(f"\n[Step 1/4] ⚡ Executing global FFT audio time alignment (Sampling Rate: {args.sr} Hz)...")
    sync_t0 = time.time()
    ref_info, target_results = sync_all_targets(
        args.ref, args.targets, sr=args.sr, sample_dur=args.sample_dur, workers=args.workers
    )
    sync_duration = time.time() - sync_t0
    print(f"  ✓ Time alignment complete! Processed {total_cams} cameras in {sync_duration:.2f}s\n")

    overlap_start, overlap_end = compute_common_overlap_range(ref_info, target_results)
    has_manual_trim = (args.ref_start is not None or args.ref_end is not None)
    t_ref_start = parse_time_to_seconds(args.ref_start) if args.ref_start is not None else overlap_start
    t_ref_end = parse_time_to_seconds(args.ref_end) if args.ref_end is not None else overlap_end

    trim_info = {
        "start": t_ref_start,
        "end": t_ref_end,
        "start_str": format_seconds(t_ref_start),
        "end_str": format_seconds(t_ref_end)
    }

    print_sync_table(ref_info, target_results, trim_info=trim_info if has_manual_trim else None)

    # ---------------------------------------------------------
    # Step 2: Global EBU R128 Audio Normalization
    # ---------------------------------------------------------
    audio_map = {}
    temp_norm_dir = None

    if args.normalize:
        print(f"\n[Step 2/4] 🎚️  Executing full-length EBU R128 audio normalization ({args.lufs} LUFS)...")
        temp_norm_dir = tempfile.TemporaryDirectory()
        audio_map, norm_total_time = normalize_all_audio_tracks(
            all_inputs, temp_norm_dir.name,
            lufs=args.lufs, lra=args.lra, tp=args.tp,
            audio_bitrate=args.audio_bitrate, workers=args.workers
        )
        print(f"  ✓ Audio normalization complete! Total time: {norm_total_time:.1f}s\n")
    else:
        print("\n[Step 2/4] 🎚️  Audio normalization: Skipped (flag --normalize not specified)")

    # ---------------------------------------------------------
    # Step 3: Chapter Pause Segmentation & Stream-Copy Slicing
    # ---------------------------------------------------------
    part_segments = None
    if args.auto_split:
        min_sec = args.split_min_dur * 60.0
        max_sec = args.split_max_dur * 60.0
        overlap_dur = overlap_end - overlap_start
        print(f"\n[Step 3/4] 🔍 Analyzing CAM1 pause points & exporting sub-clips (Target window: {args.split_min_dur:.0f}-{args.split_max_dur:.0f} min)...")
        print(f"  ℹ️  Multicam valid overlapping range: {format_seconds(overlap_start)} → {format_seconds(overlap_end)} (Total synced duration: {format_seconds(overlap_dur)})")
        split_t0 = time.time()
        split_ref_path = audio_map.get(args.ref, args.ref)
        split_points = find_natural_split_points(
            split_ref_path, start_sec=overlap_start, end_sec=overlap_end,
            min_dur_sec=min_sec, max_dur_sec=max_sec
        )
        part_segments = build_part_segments(split_points, ref_info, target_results, audio_map=audio_map)
        print(f"  ✓ Segmentation analysis complete in {time.time() - split_t0:.2f}s (Total {len(part_segments)} parts).")
        print_split_summary(part_segments)

        # Export JSON / CSV reports
        json_path = args.export_json or (os.path.join(args.output_dir, "multicam_sync.json") if args.output_dir else None)
        csv_path = args.export_csv or (os.path.join(args.output_dir, "multicam_sync.csv") if args.output_dir else None)

        if json_path:
            export_sync_json(json_path, ref_info, target_results, trim_info=trim_info, part_segments=part_segments)
            print(f"  📄 Sync and chapter metadata exported to JSON: {json_path}")

        if csv_path:
            export_sync_csv(csv_path, ref_info, target_results, trim_info=trim_info)
            print(f"  📄 Alignment table exported to CSV: {csv_path}")

        # Batch stream-copy cutting for chapter parts
        out_dir = args.output_dir or "./split_output"
        n_exp, exp_time = cut_all_split_parts(
            part_segments, out_dir, copy_codec=True,
            video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
            workers=args.workers
        )
        print(f"\n  ✓ All chapter sub-clips exported successfully! ({n_exp} files in {exp_time:.1f}s)")

        # Export full-length synchronized master camera files (*_synced.mp4)
        print(f"\n  ► Exporting full-length synchronized camera masters ({total_cams} CAMs) for NLE editing ...")
        full_sync_tasks = [
            {
                "video": args.ref,
                "audio": audio_map.get(args.ref),
                "output": os.path.join(out_dir, f"{os.path.splitext(ref_info['basename'])[0]}{args.suffix}{os.path.splitext(ref_info['basename'])[1]}"),
                "start": overlap_start,
                "end": overlap_end,
                "name": ref_info["basename"]
            }
        ]
        for idx, r in enumerate(target_results):
            tgt_start = overlap_start - r["offset_sec"]
            tgt_end = overlap_end - r["offset_sec"]
            base, ext = os.path.splitext(r["target_basename"])
            full_sync_tasks.append({
                "video": r["target_video"],
                "audio": audio_map.get(r["target_video"]),
                "output": os.path.join(out_dir, f"{base}{args.suffix}{ext}"),
                "start": tgt_start,
                "end": tgt_end,
                "name": r["target_basename"]
            })

        for stask in full_sync_tasks:
            print(f"    • Slicing {stask['name']} → {os.path.basename(stask['output'])} ...")
            cut_single_clip(
                stask["video"], stask["output"], stask["start"], stask["end"],
                norm_audio_path=stask["audio"], copy_codec=True,
                video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate
            )
        print(f"  ✓ Full-length synchronized camera masters exported!")

        # Step 4: Multi-in-One Composition for 2-6 Cameras in Each Chapter Part
        if args.merge:
            print(f"\n[Step 4/4] 🔲 Rendering Multi-in-One grid videos ({total_cams} CAMs, {cols}x{rows} grid, {cw}x{ch}/cell -> {tot_w}x{tot_h})...")
            for part in part_segments:
                part_video_paths = []
                for cam in part["cameras"]:
                    base, ext = os.path.splitext(cam["camera_name"])
                    part_video_paths.append(os.path.join(out_dir, f"{base}_{part['part_name']}{ext}"))

                merged_video_path = os.path.join(out_dir, f"multicam_merged_{part['part_name']}.mp4")
                print(f"  ► Composing Multi-in-One {part['part_name']} ({total_cams} CAMs -> {tot_w}x{tot_h}) → {os.path.basename(merged_video_path)} ...")
                t_comp = compose_multicam_video(
                    part_video_paths, merged_video_path,
                    video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
                    encoder=args.encoder
                )
                print(f"    ✓ Composed {os.path.basename(merged_video_path)} in {t_comp:.1f}s")
        else:
            print(f"\n[Step 4/4] 🔲 Multi-in-One composition: Skipped (flag --merge not specified)")

        print("\n" + "=" * 78)
        print("✅  Multi-Camera Preprocessing Pipeline Completed Successfully!")
        print("=" * 78 + "\n")
        if temp_norm_dir:
            temp_norm_dir.cleanup()
        return

    # Scenario B: Export Full Synced Video or Manual Trim Range
    if args.output_dir or has_manual_trim:
        trim_label = "Manual Trim Range" if has_manual_trim else "Full Synchronized Overlap"
        print(f"\n[Step 3/4] ✂️  Exporting {trim_label} clips ({total_cams} cameras)...")
        print(f"  Ref Range: {format_seconds(t_ref_start)} → {format_seconds(t_ref_end)} (Duration: {format_seconds(t_ref_end - t_ref_start)})")

        # Export JSON / CSV reports
        json_path = args.export_json or (os.path.join(args.output_dir, "multicam_sync.json") if args.output_dir else None)
        csv_path = args.export_csv or (os.path.join(args.output_dir, "multicam_sync.csv") if args.output_dir else None)

        if json_path:
            export_sync_json(json_path, ref_info, target_results, trim_info=trim_info, part_segments=None)
            print(f"  📄 Alignment metadata exported to JSON: {json_path}")

        if csv_path:
            export_sync_csv(csv_path, ref_info, target_results, trim_info=trim_info)
            print(f"  📄 Alignment table exported to CSV: {csv_path}")

        export_tasks = []
        if args.ref_output:
            ref_out = args.ref_output
        elif args.output_dir:
            base, ext = os.path.splitext(ref_info["basename"])
            ref_out = os.path.join(args.output_dir, f"{base}{args.suffix}{ext}")
        else:
            base, ext = os.path.splitext(ref_info["basename"])
            ref_out = f"{base}{args.suffix}{ext}"

        export_tasks.append({
            "video": args.ref,
            "audio": audio_map.get(args.ref),
            "output": ref_out,
            "start": t_ref_start,
            "end": t_ref_end,
            "name": ref_info["basename"]
        })

        for idx, r in enumerate(target_results):
            t_start = t_ref_start - r["offset_sec"]
            t_end = t_ref_end - r["offset_sec"]

            if args.target_outputs and idx < len(args.target_outputs):
                tgt_out = args.target_outputs[idx]
            elif args.output_dir:
                base, ext = os.path.splitext(r["target_basename"])
                tgt_out = os.path.join(args.output_dir, f"{base}{args.suffix}{ext}")
            else:
                base, ext = os.path.splitext(r["target_basename"])
                tgt_out = f"{base}{args.suffix}{ext}"

            export_tasks.append({
                "video": r["target_video"],
                "audio": audio_map.get(r["target_video"]),
                "output": tgt_out,
                "start": t_start,
                "end": t_end,
                "name": r["target_basename"]
            })

        for task in export_tasks:
            mode_tag = "Stream Copy + Norm Audio" if task["audio"] else "Stream Copy (-c copy)"
            print(f"\n  ► Slicing {task['name']} ({format_seconds(task['start'])} → {format_seconds(task['end'])}) [{mode_tag}] → {task['output']} ...")
            t_proc = cut_single_clip(
                task["video"], task["output"], task["start"], task["end"],
                norm_audio_path=task["audio"], copy_codec=True,
                video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate
            )
            print(f"    ✓ Finished in {t_proc:.1f}s")

        # Step 4: Multi-in-One Composition for 2-6 Cameras
        if args.merge:
            print(f"\n[Step 4/4] 🔲 Rendering Multi-in-One grid video ({total_cams} CAMs, {cols}x{rows} grid, {cw}x{ch}/cell -> {tot_w}x{tot_h})...")
            synced_video_paths = [t["output"] for t in export_tasks]
            script_dir = args.output_dir or "."
            merged_video_path = os.path.join(script_dir, "multicam_merged_synced.mp4")
            print(f"  ► Composing Multi-in-One grid video ({total_cams} CAMs -> {tot_w}x{tot_h}) → {os.path.basename(merged_video_path)} ...")
            t_comp = compose_multicam_video(
                synced_video_paths, merged_video_path,
                video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
                encoder=args.encoder
            )
            print(f"    ✓ Composed {os.path.basename(merged_video_path)} in {t_comp:.1f}s")
        else:
            print(f"\n[Step 4/4] 🔲 Multi-in-One composition: Skipped (flag --merge not specified)")

        print("\n" + "=" * 78)
        print("✅  Multi-Camera Preprocessing Completed Successfully!")
        print("=" * 78 + "\n")
        if temp_norm_dir:
            temp_norm_dir.cleanup()
        return

    # Scenario C: Alignment Report Only
    json_path = args.export_json
    csv_path = args.export_csv

    if json_path:
        export_sync_json(json_path, ref_info, target_results, trim_info=None, part_segments=None)
        print(f"  📄 Alignment metadata exported to JSON: {json_path}")

    if csv_path:
        export_sync_csv(csv_path, ref_info, target_results, trim_info=None)
        print(f"  📄 Alignment table exported to CSV: {csv_path}")

    print("\nℹ️  Time alignment analysis complete.")
    print("   - To auto-split into 30-40 min chapters for AI editing, add `--auto-split`.")
    print("   - To generate 2-6 camera multi-in-one merged video (<= 1080P canvas), add `--merge`.")
    print("   - To manually trim a range, add `--ref-start HH:MM:SS` and `--ref-end HH:MM:SS`.")
    print("=" * 78 + "\n")
    if temp_norm_dir:
        temp_norm_dir.cleanup()


if __name__ == "__main__":
    main()
