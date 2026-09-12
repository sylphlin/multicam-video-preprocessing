#!/usr/bin/env python3
"""
Multi-Camera Video Pipeline CLI Tool (Universal Pipeline Engine).
Supports 2 to 6+ Cameras with Guaranteed Compact Canvas (Max <= 1920x1080, Min >= 640x360/CAM).
Zero-Split Architecture for Gemini 3.8 Flash Agentic Video Understanding.
Modular Architecture:
  1. Global Audio Time Alignment (modules.audio_sync)
  2. Full-Length EBU R128 Audio Loudness Normalization (modules.audio_normalizer)
  3. Full-Length Synchronized Camera Masters Export (modules.video_composer)
  4. Multi-in-One Grid Video Composition for 2-6+ Cameras (modules.video_composer)
  5. Reporting and Data Export (modules.reporter)
  6. Time Utility Conversions (modules.time_utils)

CLI Examples:
  # Example 1: Standard Agentic Zero-Split Pipeline (Sync + EBU R128 + Synced Masters + Full Grid Merge)
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --normalize --merge \
    --output-dir ./output/

  # Example 2: Multi-Camera Time Alignment Analysis Only
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 CAM3.mp4
"""

import argparse
import concurrent.futures
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.time_utils import parse_time_to_seconds, format_seconds
from modules.audio_sync import sync_all_targets, compute_common_overlap_range, SCORE_LOW
from modules.audio_normalizer import normalize_all_audio_tracks
from modules.video_composer import (
    compute_grid_spec, compose_multicam_video, cut_single_clip
)
from modules.reporter import (
    print_sync_table, export_sync_json, export_sync_csv
)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Camera Video Preprocessing CLI: Global Time Sync + EBU R128 Loudness Normalization + Full Synced Masters + Multi-in-One Grid Composition (Zero-Split Agentic Architecture)",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    # Camera Inputs (Supports 2 to 6+ Cameras)
    parser.add_argument("--ref", required=True, help="Reference anchor camera video path (CAM1)")
    parser.add_argument("--targets", "--target", nargs="+", required=True, help="One or more target camera video paths (CAM2, CAM3... up to CAM6)")

    # Manual Trim Range (Optional)
    parser.add_argument("--ref-start", default=None, help="Reference camera manual start time (HH:MM:SS.mmm or seconds)")
    parser.add_argument("--ref-end", default=None, help="Reference camera manual end time (HH:MM:SS.mmm or seconds)")

    # Multi-in-One Merging (AI Model Token Optimization)
    parser.add_argument("--merge", "--multi-in-one", dest="merge", action="store_true", help="Render merged multi-in-one grid video to save tokens for Agentic Video")
    parser.add_argument("--encoder", default="h264_videotoolbox", help="Video encoder for rendering (default: h264_videotoolbox, fallback: libx264)")
    parser.add_argument("--stream-copy", action="store_true", help="Export synchronized camera masters using raw stream-copy (-c copy) instead of frame-accurate re-encoding")

    # Output & Naming Controls
    parser.add_argument("-o", "--output-dir", dest="output_dir", default=None, help="Output directory for synced camera masters, grid video, and reports")
    parser.add_argument("--suffix", default="_synced", help="Filename suffix for synchronized export (default: _synced)")
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
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker threads (default: 4)")
    parser.add_argument("--full-scan", action="store_true", help="Force full-length MFCC scan (skip fast 120s search ladder)")
    parser.add_argument("--no-subframe-refine", action="store_true", help="Disable sub-frame refinement (stay at hop-level MFCC resolution)")
    parser.add_argument("--strict-sync", action="store_true", help="Abort and exit non-zero if any camera audio alignment confidence is low (score < 7.0)")

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

    # Compute grid layout specifications (Max <= 1920x1080, Min >= 640x360)
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
        args.ref, args.targets, sr=args.sr, sample_dur=args.sample_dur, workers=args.workers,
        full_scan=args.full_scan, refine_subframe=not args.no_subframe_refine
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

    # Summary Gate: verify audio alignment confidence across all target cameras
    low_conf_targets = [r for r in target_results if r.get("peak_z_score", 0.0) < SCORE_LOW]
    if low_conf_targets:
        delim = "=" * 78
        sub_delim = "-" * 78
        lines = [
            f"\n{delim}",
            f"⚠️  WARNING: LOW CONFIDENCE AUDIO ALIGNMENT DETECTED ({len(low_conf_targets)} camera(s) affected)",
            sub_delim,
        ]
        for r in low_conf_targets:
            lines.append(
                f"  • {r['target_basename']}: Score {r['peak_z_score']:.1f}, "
                f"Confidence {r['confidence']:.1f}%, Offset {r['offset_sec']:+.3f}s"
            )
        lines.extend([
            "",
            "The exported masters, merged grid video, and subsequent AI EDL/subtitles",
            "will inherit this misalignment!",
            "Common causes:",
            "  - Cameras share no audible content or one recording is silent.",
            "  - --sample-dur covers only a silent section.",
            "Possible remedies:",
            "  - Re-run with --full-scan to analyze the full recordings.",
            "  - Set the active range manually using --ref-start / --ref-end.",
            f"{delim}\n"
        ])
        warning_block = "\n".join(lines)
        print(warning_block)
        sys.stderr.write(warning_block)
        sys.stderr.flush()

        if args.strict_sync:
            print(
                f"[Error] Aborting pipeline due to --strict-sync: {len(low_conf_targets)} camera(s) failed confidence threshold (score < {SCORE_LOW:.1f}).\n",
                file=sys.stderr
            )
            sys.exit(1)

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
    # Step 3: Export Full Synced Masters or Manual Trim Range
    # ---------------------------------------------------------
    if args.output_dir or has_manual_trim:
        trim_label = "Manual Trim Range" if has_manual_trim else "Full Synchronized Overlap"
        print(f"\n[Step 3/4] ✂️  Exporting {trim_label} masters ({total_cams} cameras)...")
        print(f"  Ref Range: {format_seconds(t_ref_start)} → {format_seconds(t_ref_end)} (Duration: {format_seconds(t_ref_end - t_ref_start)})")

        # Export JSON / CSV reports
        json_path = args.export_json or (os.path.join(args.output_dir, "multicam_sync.json") if args.output_dir else None)
        csv_path = args.export_csv or (os.path.join(args.output_dir, "multicam_sync.csv") if args.output_dir else None)

        if json_path:
            export_sync_json(json_path, ref_info, target_results, trim_info=trim_info)
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

        mode_str = "Lossless Stream Copy (-c copy)" if args.stream_copy else f"Frame-Accurate Re-encode ({args.encoder})"
        print(f"\n  ► Exporting full-length synchronized camera masters ({total_cams} CAMs) in parallel [{mode_str}] ...")
        t_masters_start = time.time()

        def _export_single_task(stask):
            t_s_0 = time.time()
            cut_single_clip(
                stask["video"], stask["output"], stask["start"], stask["end"],
                norm_audio_path=stask["audio"], copy_codec=args.stream_copy,
                video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
                encoder=args.encoder
            )
            return stask["name"], os.path.basename(stask["output"]), time.time() - t_s_0

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(export_tasks), args.workers)) as executor:
            e_futures = [executor.submit(_export_single_task, st) for st in export_tasks]
            for fut in concurrent.futures.as_completed(e_futures):
                src_name, dst_name, dur = fut.result()
                print(f"    ✓ Sliced {src_name} → {dst_name} ({dur:.1f}s)")

        print(f"  ✓ All synchronized camera masters exported in {time.time() - t_masters_start:.1f}s!")

        # ---------------------------------------------------------
        # Step 4: Multi-in-One Full Grid Video Composition
        # ---------------------------------------------------------
        if args.merge:
            print(f"\n[Step 4/4] 🔲 Rendering Multi-in-One grid video ({total_cams} CAMs, {cols}x{rows} grid, {cw}x{ch}/cell -> {tot_w}x{tot_h})...")
            synced_video_paths = [t["output"] for t in export_tasks]
            script_dir = args.output_dir or "."
            merged_video_path = os.path.join(script_dir, "multicam_merged_full.mp4")
            print(f"  ► Composing Multi-in-One grid video ({total_cams} CAMs -> {tot_w}x{tot_h}) → {os.path.basename(merged_video_path)} ...")
            t_comp = compose_multicam_video(
                synced_video_paths, merged_video_path,
                video_bitrate=args.video_bitrate, audio_bitrate=args.audio_bitrate,
                encoder=args.encoder
            )
            print(f"    ✓ Composed {os.path.basename(merged_video_path)} in {t_comp:.1f}s")
            # Create compatibility symlink/alias multicam_merged_synced.mp4
            compat_path = os.path.join(script_dir, "multicam_merged_synced.mp4")
            if not os.path.exists(compat_path):
                try:
                    os.symlink(os.path.basename(merged_video_path), compat_path)
                except OSError:
                    pass
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
        export_sync_json(json_path, ref_info, target_results, trim_info=None)
        print(f"  📄 Alignment metadata exported to JSON: {json_path}")

    if csv_path:
        export_sync_csv(csv_path, ref_info, target_results, trim_info=None)
        print(f"  📄 Alignment table exported to CSV: {csv_path}")

    print("\nℹ️  Time alignment analysis complete.")
    print("   - To export synchronized masters and multi-in-one grid, add `--normalize --merge -o <DIR>`.")
    print("   - To manually trim a range, add `--ref-start HH:MM:SS` and `--ref-end HH:MM:SS`.")
    print("=" * 78 + "\n")
    if temp_norm_dir:
        temp_norm_dir.cleanup()


if __name__ == "__main__":
    main()
