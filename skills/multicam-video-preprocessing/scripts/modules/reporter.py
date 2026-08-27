"""
Multi-camera reporting and data export module (Reporter Module).
Prints formatted terminal matrices and exports JSON and CSV alignment metadata.
"""

import csv
import json
import os
from .time_utils import format_seconds


def print_sync_table(ref_info, target_results, trim_info=None):
    """
    Print multi-camera time alignment matrix to the terminal.
    """
    total_cams = len(target_results) + 1
    print("-" * 78)
    print(f"Reference Camera (Anchor): {ref_info['basename']} (Duration: {format_seconds(ref_info['duration_sec'])})")
    if trim_info and trim_info.get("start") is not None:
        t_s = trim_info["start"]
        t_e = trim_info["end"]
        dur = t_e - t_s
        print(f"Trim Range: {format_seconds(t_s)} → {format_seconds(t_e)} (Duration: {format_seconds(dur)})")
    print("-" * 78)
    status_header = "Trim Start → End" if (trim_info and trim_info.get("start") is not None) else "Status"
    print(f"{'Camera Name':<22} | {'Offset (Delta t)':<18} | {'Conf':<8} | {status_header}")
    print("-" * 78)

    # Reference
    if trim_info and trim_info.get("start") is not None:
        ref_status = f"{format_seconds(trim_info['start'])} → {format_seconds(trim_info['end'])}"
    else:
        ref_status = "Anchor (Reference)"
    print(f"{ref_info['basename'][:22]:<22} | {'0.000000s (Anchor)':<18} | {'100%':<8} | {ref_status}")

    # Targets
    for r in target_results:
        off = r["offset_sec"]
        conf = r["confidence"]
        dir_str = "Late" if off > 0 else ("Early" if off < 0 else "Sync")
        off_display = f"{off:+.3f}s ({dir_str})"

        if conf >= 85:
            conf_display = f"✓ {conf:.1f}%"
        elif conf >= 65:
            conf_display = f"ℹ {conf:.1f}%"
        else:
            conf_display = f"⚠️ {conf:.1f}%"

        if trim_info and trim_info.get("start") is not None:
            t_tgt_start = trim_info["start"] - off
            t_tgt_end = trim_info["end"] - off
            status_display = f"{format_seconds(t_tgt_start)} → {format_seconds(t_tgt_end)}"
        else:
            status_display = "Aligned"

        print(f"{r['target_basename'][:22]:<22} | {off_display:<18} | {conf_display:<8} | {status_display}")
    print("-" * 78)


def print_split_summary(part_segments):
    """
    Print chapter segmentation summary.
    """
    print("\n" + "=" * 78)
    print(f"📑  Automatic Chapter Segmentation (Total {len(part_segments)} Parts)")
    print("=" * 78)
    for p in part_segments:
        print(f"\n  [{p['part_name'].upper()}] Duration: {format_seconds(p['duration_sec'])} | Ref Time: {format_seconds(p['ref_start'])} → {format_seconds(p['ref_end'])}")
        print("  " + "-" * 74)
        for cam in p["cameras"]:
            print(f"    • {cam['camera_name']:<22} : {format_seconds(cam['start_sec'])} → {format_seconds(cam['end_sec'])}")
    print("=" * 78)


def export_sync_json(filepath, ref_info, target_results, trim_info=None, part_segments=None):
    """
    Export structured JSON metadata.
    """
    cameras_meta = [{
        "camera": ref_info["basename"],
        "is_ref": True,
        "offset_sec": 0.0,
        "confidence": 100.0,
        "duration_sec": ref_info["duration_sec"]
    }]

    for r in target_results:
        cameras_meta.append({
            "camera": r["target_basename"],
            "is_ref": False,
            "offset_sec": r["offset_sec"],
            "confidence": r["confidence"],
            "duration_sec": r["duration_sec"]
        })

    data = {
        "ref_video": ref_info["path"],
        "cameras": cameras_meta
    }

    if trim_info:
        data["trim"] = {
            "ref_start": trim_info.get("start_str"),
            "ref_end": trim_info.get("end_str")
        }

    if part_segments:
        data["parts"] = part_segments

    with open(filepath, "w", encoding="utf-8") as jf:
        json.dump(data, jf, ensure_ascii=False, indent=2)


def export_sync_csv(filepath, ref_info, target_results, trim_info=None):
    """
    Export CSV alignment table.
    """
    with open(filepath, "w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(["Camera", "Is_Reference", "Offset_Seconds", "Confidence_Percent", "Trim_Start", "Trim_End"])

        # Ref
        t_start_s = trim_info.get("start_str") if trim_info else ""
        t_end_s = trim_info.get("end_str") if trim_info else ""
        writer.writerow([ref_info["basename"], "TRUE", "0.000000", "100.0", t_start_s, t_end_s])

        for r in target_results:
            off = r["offset_sec"]
            if trim_info and trim_info.get("start") is not None:
                tgt_s = format_seconds(trim_info["start"] - off)
                tgt_e = format_seconds(trim_info["end"] - off)
            else:
                tgt_s, tgt_e = "", ""

            writer.writerow([r["target_basename"], "FALSE", f"{off:+.6f}", f"{r['confidence']:.1f}", tgt_s, tgt_e])
