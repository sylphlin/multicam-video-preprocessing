#!/usr/bin/env python3
"""
FCP7 XML Exporter CLI Tool (export_fcp7_xml.py).
Converts multi-camera EDL CSV files (e.g. edl_part1.csv, edl_part2.csv) into a seamless Final Cut Pro 7 XML (xmeml version 4)
timeline file for professional NLEs (DaVinci Resolve, Premiere Pro, Final Cut Pro).

Features:
  - Multi-Part Timeline Continuity: Seamlessly chains multiple sequential chapter EDLs (part1, part2...) with frame-accurate timeline offset accumulation.
  - Dual Media Reference Modes:
      1. Part Clip Linking (Default): References cut chapter media files (*_part1.mp4, *_part2.mp4).
      2. Raw Original Camera Linking (--use-raw-media): Uses multicam_sync.json global sync offsets to link directly to full un-sliced camera originals.
  - Rich Timeline Markers: Color-coded markers for editing rules ([強制] -> Red, [一般] -> Blue) with full reason comments.
  - Multi-Track Audio Mapping: Synchronized master host audio tracks (CAM1) across the entire sequence timeline.
  - Safe URI Path Encoding: Robust path cleaning and URL-encoding (file://localhost/...) for cross-platform NLE relinking.

Usage Examples:
  # Example 1: Auto-discover all parts in directory and export unified full timeline XML
  python3 scripts/export_fcp7_xml.py -d ./test/full_pipeline_output/

  # Example 2: Export specific EDL CSV files into unified XML
  python3 scripts/export_fcp7_xml.py \
    -e edl_part1.csv edl_part2.csv \
    -o final_cut_full.xml

  # Example 3: Export single part XML
  python3 scripts/export_fcp7_xml.py \
    -e edl_part1.csv \
    -o final_cut_part1.xml
"""

import argparse
import csv
import glob
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse


DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
AUDIO_SAMPLE_RATE = 48000
AUDIO_DEPTH = 16


def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def time_str_to_frames(time_str, fps=DEFAULT_FPS):
    if not time_str:
        return 0
    try:
        t_str = str(time_str).strip().replace('"', "").replace("'", "")
        if not t_str:
            return 0
        if ":" in t_str:
            parts = t_str.split(":")
            if len(parts) == 3:
                h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                sec = h * 3600.0 + m * 60.0 + s
            elif len(parts) == 2:
                m, s = float(parts[0]), float(parts[1])
                sec = m * 60.0 + s
            else:
                sec = float(parts[0])
        else:
            sec = float(t_str)
        return int(round(sec * fps))
    except Exception as e:
        print(f"[Warning] Failed to parse timecode '{time_str}': {e}", file=sys.stderr)
        return 0


def clean_input_path(raw_path):
    """Clean path by stripping file:// prefixes, decoding URLs, and making absolute."""
    if raw_path.startswith("file://localhost"):
        raw_path = raw_path.replace("file://localhost", "")
    elif raw_path.startswith("file://"):
        raw_path = raw_path.replace("file://", "")

    decoded_path = urllib.parse.unquote(raw_path)
    if os.name == "nt" and decoded_path.startswith("/") and ":" in decoded_path:
        decoded_path = decoded_path.lstrip("/")

    return os.path.abspath(decoded_path)


def format_path_for_xml(system_path):
    """Convert absolute path to URL-encoded file://localhost URI format for XML."""
    path_obj = Path(system_path)
    path_str = path_obj.as_posix()
    if not path_str.startswith("/"):
        path_str = "/" + path_str
    encoded_path = urllib.parse.quote(path_str, safe="/:")
    return f"file://localhost{encoded_path}"


def auto_discover_camera_files(media_dir, part_tag=None):
    """
    Discover camera files in media_dir.
    Prioritizes full synchronized master files (*_synced.mp4), then part files if part_tag is given.
    """
    if not media_dir or not os.path.exists(media_dir):
        return {}
    mapping = {}
    all_files = sorted(os.listdir(media_dir))
    candidates = []

    # 1. First priority: full synchronized camera master files (*_synced.mp4)
    synced_files = [
        os.path.join(media_dir, f) for f in all_files
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".m4v"))
        and "synced" in f.lower() and "merged" not in f.lower() and "final" not in f.lower()
    ]
    if synced_files:
        candidates = synced_files

    # 2. Second priority: filter by part_tag if specified
    if not candidates and part_tag:
        pt_lower = part_tag.lower()
        for f in all_files:
            if any(f.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m4v")):
                if "merged" not in f.lower() and "final" not in f.lower() and "seg_" not in f.lower():
                    if pt_lower in f.lower():
                        candidates.append(os.path.join(media_dir, f))

    # 3. Fallback: any valid camera video files
    if not candidates:
        for f in all_files:
            if any(f.lower().endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".m4v")):
                if "merged" not in f.lower() and "final" not in f.lower() and "seg_" not in f.lower():
                    candidates.append(os.path.join(media_dir, f))

    candidates.sort(key=natural_sort_key)
    for idx, fpath in enumerate(candidates, start=1):
        mapping[f"CAM{idx}"] = fpath
        mapping[f"cam{idx}"] = fpath
        mapping[f"CAM_{idx}"] = fpath
        mapping[f"C{idx}"] = fpath
    return mapping


def load_edl_csv_records(csv_path):
    """Load and parse an EDL CSV file."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"EDL CSV file not found: {csv_path}")

    records = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample and "," not in sample else ","
        reader = csv.reader(f, delimiter=delimiter)
        rows = list(reader)

    if not rows:
        return records

    header_row_idx = 0
    for r_idx, row in enumerate(rows[:5]):
        row_str = " ".join(row).lower()
        if any(kw in row_str for kw in ("start", "best_camera", "camera", "cam")):
            header_row_idx = r_idx
            break

    header = [h.strip().lower() for h in rows[header_row_idx]]
    start_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("start", "in", "from"))), 0)
    end_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("end", "out", "to"))), 1)
    cam_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("cam", "source", "clip"))), 2)
    rule_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("規則", "rule"))), -1)
    reason_idx = next((i for i, h in enumerate(header) if any(kw in h for kw in ("原因", "reason", "note", "desc"))), -1)

    for row in rows[header_row_idx + 1:]:
        if not row or len(row) < 3:
            continue
        start_str = row[start_idx].strip() if len(row) > start_idx else ""
        end_str = row[end_idx].strip() if len(row) > end_idx else ""
        cam_str = row[cam_idx].strip() if len(row) > cam_idx else ""
        if not start_str or not end_str or not cam_str:
            continue

        rule_str = row[rule_idx].strip() if rule_idx >= 0 and len(row) > rule_idx else ""
        reason_str = row[reason_idx].strip() if reason_idx >= 0 and len(row) > reason_idx else ""

        records.append({
            "start_str": start_str,
            "end_str": end_str,
            "camera": cam_str,
            "rule": rule_str,
            "reason": reason_str
        })
    return records


_DURATION_CACHE = {}


def probe_media_duration_frames(file_path, fps=DEFAULT_FPS):
    """Probe actual duration in frames of a video file using ffprobe."""
    if not file_path or not os.path.exists(file_path):
        return 200000
    if file_path in _DURATION_CACHE:
        return _DURATION_CACHE[file_path]
    try:
        import subprocess
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file_path]
        res = subprocess.check_output(cmd, text=True).strip()
        sec = float(res)
        dur_frames = int(round(sec * fps))
        _DURATION_CACHE[file_path] = dur_frames
        return dur_frames
    except Exception:
        return 200000


def create_file_node(file_id, filename, file_url, fps, duration, width, height):
    """Generate standard FCP7 XML <file> node with Reel name matching reference implementation."""
    reel_name = os.path.splitext(filename)[0]
    return f"""
                    <file id="{file_id}">
                        <name>{filename}</name>
                        <pathurl>{file_url}</pathurl>
                        <rate><timebase>{fps}</timebase></rate>
                        <duration>{duration}</duration>
                        <timecode>
                            <rate><timebase>{fps}</timebase></rate>
                            <string>00:00:00:00</string>
                            <frame>0</frame>
                            <displayformat>NDF</displayformat>
                            <reel>
                                <name>{reel_name}</name>
                            </reel>
                        </timecode>
                        <media>
                            <video>
                                <samplecharacteristics>
                                    <rate><timebase>{fps}</timebase></rate>
                                    <width>{width}</width>
                                    <height>{height}</height>
                                    <pixelaspectratio>square</pixelaspectratio>
                                </samplecharacteristics>
                            </video>
                            <audio>
                                <samplecharacteristics>
                                    <depth>{AUDIO_DEPTH}</depth>
                                    <samplerate>{AUDIO_SAMPLE_RATE}</samplerate>
                                </samplecharacteristics>
                                <channelcount>2</channelcount>
                            </audio>
                        </media>
                    </file>"""


def build_fcp7_xml_sequence(all_part_clips, part_audio_list=None, seq_name="final_cut_full",
                            fps=DEFAULT_FPS, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
    """
    Construct standard Final Cut Pro 7 XML (xmeml version 4) content matching working reference.
    """
    total_timeline_duration = all_part_clips[-1]["timeline_end"] if all_part_clips else 0
    is_ntsc = "TRUE" if fps % 30 == 0 or fps == 24 else "FALSE"

    xml_header = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
<sequence id="sequence-1">
    <name>{seq_name}</name>
    <duration>{total_timeline_duration}</duration>
    <rate>
        <timebase>{fps}</timebase>
        <ntsc>{is_ntsc}</ntsc>
    </rate>
    <media>
        <video>
            <format>
                <samplecharacteristics>
                    <rate><timebase>{fps}</timebase></rate>
                    <width>{width}</width>
                    <height>{height}</height>
                    <pixelaspectratio>square</pixelaspectratio>
                </samplecharacteristics>
            </format>
            <track>
"""

    xml_video_body = ""
    file_id_map = {}

    for i, clip in enumerate(all_part_clips, start=1):
        cam_key = clip["camera"]
        real_file_path = clip.get("file_path") or f"MISSING_{cam_key}.mp4"
        file_url = format_path_for_xml(real_file_path)
        filename = Path(real_file_path).name

        lookup_key = cam_key if "synced" in real_file_path.lower() else f"{clip.get('part_tag', '')}_{cam_key}"
        if lookup_key not in file_id_map:
            file_id_map[lookup_key] = f"masterclip-{lookup_key}"

        master_file_id = file_id_map[lookup_key]
        clip_id = f"video-item-{i}"
        clip_dur = clip["source_out"] - clip["source_in"]

        file_node = create_file_node(master_file_id, filename, file_url, fps, total_timeline_duration + 50000, width, height)

        marker_color = "(255,0,0)" if "[強制]" in clip["rule"] else "(0,0,255)"
        clean_rule = clip["rule"].replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        clean_reason = clip["reason"].replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

        xml_video_body += f"""
                <clipitem id="{clip_id}">
                    <name>{cam_key}</name>
                    <enabled>TRUE</enabled>
                    <duration>{clip_dur}</duration>
                    <rate><timebase>{fps}</timebase></rate>
                    <start>{clip['timeline_start']}</start>
                    <end>{clip['timeline_end']}</end>
                    <in>{clip['source_in']}</in>
                    <out>{clip['source_out']}</out>
                    {file_node}
                    <marker>
                        <name>{clean_rule}</name>
                        <comment>{clean_reason}</comment>
                        <in>{clip['source_in']}</in>
                        <out>{clip['source_in'] + 1}</out>
                        <rgb>{marker_color}</rgb>
                    </marker>
                </clipitem>
"""

    xml_video_end = """
            </track>
        </video>
"""

    # Audio Track Section: Aligned across parts or continuous
    part_audio_list = part_audio_list or []
    if part_audio_list:
        tracks_xml = ""
        for track_idx in [1, 2]:
            tracks_xml += f"""
            <track>"""
            for a_idx, a_part in enumerate(part_audio_list, start=1):
                a_fpath = a_part["audio_path"]
                a_url = format_path_for_xml(a_fpath)
                a_fname = Path(a_fpath).name
                a_lookup = "CAM1" if "synced" in a_fpath.lower() else f"{a_part['part_tag']}-CAM1"
                a_master_id = f"masterclip-{a_lookup}"
                a_clip_id = f"audio-track{track_idx}-part{a_idx}"
                a_dur_frames = a_part["end_frame"] - a_part["start_frame"]

                a_file_node = create_file_node(a_master_id, a_fname, a_url, fps, total_timeline_duration + 50000, width, height)

                tracks_xml += f"""
                <clipitem id="{a_clip_id}">
                    <name>CAM1 Audio</name>
                    <enabled>TRUE</enabled>
                    <duration>{a_dur_frames}</duration>
                    <rate><timebase>{fps}</timebase></rate>
                    <start>{a_part['start_frame']}</start>
                    <end>{a_part['end_frame']}</end>
                    <in>{a_part['source_in']}</in>
                    <out>{a_part['source_in'] + a_dur_frames}</out>
                    {a_file_node}
                    <sourcetrack>
                        <mediatype>audio</mediatype>
                        <trackindex>{track_idx}</trackindex>
                    </sourcetrack>
                </clipitem>"""
            tracks_xml += """
            </track>"""
        xml_audio_body = f"<audio>{tracks_xml}</audio>"
    else:
        xml_audio_body = "<audio></audio>"

    xml_footer = "</media></sequence></xmeml>\n"
    return xml_header + xml_video_body + xml_video_end + xml_audio_body + xml_footer


def export_fcp7_xml_pipeline(edl_files, output_path=None, media_dir=None, sync_json=None,
                             fps=DEFAULT_FPS, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                             use_raw_media=False):
    """
    Main pipeline to convert sequential EDL CSVs into a continuous FCP7 XML sequence.
    """
    if not edl_files:
        raise ValueError("No EDL CSV files provided for XML export.")

    edl_files = sorted(edl_files, key=natural_sort_key)
    num_parts = len(edl_files)

    # Resolve output XML path
    if not output_path:
        first_dir = os.path.dirname(os.path.abspath(edl_files[0]))
        if num_parts == 1:
            base_name = os.path.splitext(os.path.basename(edl_files[0]))[0]
            output_path = os.path.join(first_dir, f"{base_name.replace('edl_', 'final_cut_')}.xml")
        else:
            output_path = os.path.join(first_dir, "final_cut_full.xml")

    media_dir = media_dir or os.path.dirname(os.path.abspath(edl_files[0]))

    # Load sync_json metadata if available
    sync_metadata = None
    if sync_json and os.path.exists(sync_json):
        with open(sync_json, "r", encoding="utf-8") as f:
            sync_metadata = json.load(f)
    elif os.path.exists(os.path.join(media_dir, "multicam_sync.json")):
        with open(os.path.join(media_dir, "multicam_sync.json"), "r", encoding="utf-8") as f:
            sync_metadata = json.load(f)

    print("\n" + "=" * 78)
    print(f"🎬  FCP7 XML Exporter: Converting {num_parts} EDL Part(s) to Final Cut Pro 7 XML")
    print("=" * 78)
    print(f"  • Target XML Output : {output_path}")
    print(f"  • Media Directory   : {media_dir}")
    print(f"  • Sequence Rate     : {fps} fps ({width}x{height})")
    print(f"  • Media Source Mode : {'Original Raw Camera Footage' if use_raw_media else 'Chapter Part Video Clips'}")
    print("-" * 78)

    all_timeline_clips = []
    accumulated_part_offset = 0
    part_audio_list = []

    for part_idx, edl_path in enumerate(edl_files, start=1):
        edl_bname = os.path.basename(edl_path)
        part_match = re.search(r"(part\d+)", edl_bname, re.IGNORECASE)
        part_tag = part_match.group(1).lower() if part_match else f"part{part_idx}"

        records = load_edl_csv_records(edl_path)
        if not records:
            print(f"  [Warning] No valid records in {edl_bname}, skipping...")
            continue

        # Auto-discover media for this specific part
        cam_map = auto_discover_camera_files(media_dir, part_tag=part_tag)

        # Raw media lookup if use_raw_media is requested
        part_sync_info = None
        if sync_metadata and "parts" in sync_metadata:
            part_sync_info = next((p for p in sync_metadata["parts"] if p.get("part_name") == part_tag or p.get("part_index") == part_idx), None)

        part_clip_count = len(records)
        part_max_out_frame = 0

        for rec in records:
            in_frame = time_str_to_frames(rec["start_str"], fps)
            out_frame = time_str_to_frames(rec["end_str"], fps)
            clip_dur = max(1, out_frame - in_frame)
            if out_frame > part_max_out_frame:
                part_max_out_frame = out_frame

            cam_name = rec["camera"].strip()
            cam_upper = cam_name.upper()

            # Resolve file path
            if use_raw_media and part_sync_info:
                # Calculate global offset inside original raw camera file
                cam_num_match = re.search(r"\d+", cam_upper)
                cam_order = int(cam_num_match.group(0)) - 1 if cam_num_match else 0
                c_list = part_sync_info.get("cameras", [])
                cam_sync = c_list[cam_order] if 0 <= cam_order < len(c_list) else None

                if cam_sync:
                    raw_start_sec = cam_sync.get("start_sec", 0.0)
                    global_offset_frames = int(round(raw_start_sec * fps))
                    source_in = global_offset_frames + in_frame
                    source_out = global_offset_frames + out_frame
                    fpath = os.path.abspath(cam_sync.get("camera_path")) if cam_sync.get("camera_path") else cam_map.get(cam_upper)
                else:
                    source_in = in_frame
                    source_out = out_frame
                    fpath = cam_map.get(cam_upper) or cam_map.get(cam_name)
            else:
                fpath = cam_map.get(cam_upper) or cam_map.get(cam_name)
                # If using full synced master footage, offset source in/out by accumulated part offset
                if fpath and "synced" in fpath.lower():
                    source_in = accumulated_part_offset + in_frame
                    source_out = accumulated_part_offset + out_frame
                else:
                    source_in = in_frame
                    source_out = out_frame

            all_timeline_clips.append({
                "part_tag": part_tag,
                "camera": cam_name,
                "file_path": fpath,
                "source_in": source_in,
                "source_out": source_out,
                "timeline_start": accumulated_part_offset + in_frame,
                "timeline_end": accumulated_part_offset + out_frame,
                "rule": rec["rule"],
                "reason": rec["reason"]
            })

        # Calculate part duration for offset accumulation
        if part_sync_info and "duration_sec" in part_sync_info:
            part_actual_dur = int(round(part_sync_info["duration_sec"] * fps))
        else:
            part_actual_dur = part_max_out_frame

        part_dur_sec = part_actual_dur / fps
        print(f"  [Part {part_idx}/{num_parts}] {edl_bname:<20} | {part_clip_count} cuts | Part Duration: {part_dur_sec:.2f}s -> Next Part Offset: {accumulated_part_offset + part_actual_dur} frames ({(accumulated_part_offset + part_actual_dur)/fps:.2f}s)")
        accumulated_part_offset += part_actual_dur

    # Construct audio tracks
    part_audio_list = []
    cam1_synced = auto_discover_camera_files(media_dir).get("CAM1")
    if cam1_synced and "synced" in cam1_synced.lower():
        # Single continuous master host audio track
        part_audio_list.append({
            "part_tag": "full",
            "audio_path": cam1_synced,
            "start_frame": 0,
            "end_frame": accumulated_part_offset,
            "source_in": 0
        })
    else:
        # Sequential audio tracks per part
        acc_offset = 0
        for p_idx, edl_path in enumerate(edl_files, start=1):
            p_bname = os.path.basename(edl_path)
            p_match = re.search(r"(part\d+)", p_bname, re.IGNORECASE)
            p_tag = p_match.group(1).lower() if p_match else f"part{p_idx}"
            p_cam1 = auto_discover_camera_files(media_dir, part_tag=p_tag).get("CAM1") or auto_discover_camera_files(media_dir).get("CAM1")
            p_dur = probe_media_duration_frames(p_cam1, fps=fps) if p_cam1 else 0
            if p_cam1:
                part_audio_list.append({
                    "part_tag": p_tag,
                    "audio_path": p_cam1,
                    "start_frame": acc_offset,
                    "end_frame": acc_offset + p_dur,
                    "source_in": 0
                })
            acc_offset += p_dur

    seq_name = os.path.splitext(os.path.basename(output_path))[0]
    xml_content = build_fcp7_xml_sequence(
        all_part_clips=all_timeline_clips,
        part_audio_list=part_audio_list,
        seq_name=seq_name,
        fps=fps,
        width=width,
        height=height
    )

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_content)

    total_sec = accumulated_part_offset / fps
    total_min = total_sec / 60.0

    print("\n" + "=" * 78)
    print("✅  FCP7 XML Export Completed Successfully!")
    print(f"  • Exported XML File: {output_path}")
    print(f"  • Total Cuts       : {len(all_timeline_clips)} clips across {num_parts} parts")
    print(f"  • Total Duration   : {int(total_sec // 60):02d}:{total_sec % 60:06.3f} ({total_sec:.2f}s / {total_min:.1f} mins)")
    print(f"  • Timeline Frames  : {accumulated_part_offset} frames @ {fps} fps")
    print("  ► Ready for import into DaVinci Resolve / Premiere Pro / Final Cut Pro!")
    print("=" * 78 + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="FCP7 XML Exporter CLI: Convert multi-camera EDL CSV files into Final Cut Pro 7 XML for DaVinci Resolve / Premiere Pro.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-e", "--edl", nargs="+", default=None, help="One or more EDL CSV files (e.g. edl_part1.csv edl_part2.csv)")
    parser.add_argument("-d", "--dir", default=None, help="Directory containing EDL CSV files (auto-discovers edl_part*.csv)")
    parser.add_argument("-o", "--output", default=None, help="Path to output FCP7 XML file (default: final_cut_full.xml)")
    parser.add_argument("-m", "--media-dir", default=None, help="Directory containing camera media files (defaults to EDL directory)")
    parser.add_argument("-s", "--sync-json", default=None, help="Path to multicam_sync.json for raw camera offset resolution")
    parser.add_argument("--use-raw-media", action="store_true", help="Link to original raw camera footage instead of chapter sub-clips")

    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="Sequence frame rate (default: 30)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Sequence width (default: 1920)")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="Sequence height (default: 1080)")

    args = parser.parse_args()

    edl_files = []
    if args.edl:
        for item in args.edl:
            expanded = glob.glob(item)
            if expanded:
                edl_files.extend(expanded)
            elif os.path.exists(item):
                edl_files.append(item)
    elif args.dir and os.path.exists(args.dir):
        pat = os.path.join(args.dir, "**/edl_*.csv")
        edl_files = glob.glob(pat, recursive=True)
        if not edl_files:
            pat_fallback = os.path.join(args.dir, "**/*.csv")
            edl_files = [f for f in glob.glob(pat_fallback, recursive=True) if "sync" not in os.path.basename(f).lower()]

    if not edl_files:
        print("[Error] No EDL CSV files found. Please specify -e/--edl or -d/--dir.", file=sys.stderr)
        sys.exit(1)

    try:
        export_fcp7_xml_pipeline(
            edl_files=edl_files,
            output_path=args.output,
            media_dir=args.media_dir,
            sync_json=args.sync_json,
            fps=args.fps,
            width=args.width,
            height=args.height,
            use_raw_media=args.use_raw_media
        )
    except Exception as e:
        print(f"\n[Error] FCP7 XML export failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
