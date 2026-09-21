#!/usr/bin/env python3
"""
FCP7 XML Exporter CLI Tool (export_fcp7_xml.py).
Converts multi-camera EDL CSV files (e.g. edl_full.csv) into a seamless Final Cut Pro 7 XML (xmeml version 4)
timeline file for professional NLEs (DaVinci Resolve, Premiere Pro, Final Cut Pro).

Features:
  - Full Timeline Continuity: Translates agentic video EDL decisions into frame-accurate NLE timelines.
  - Dual Media Reference Modes:
      1. Synchronized Camera Masters (Default): References aligned camera master files (*_synced.mp4).
      2. Raw Original Camera Linking (--use-raw-media): Uses multicam_sync.json global sync offsets to link directly to full un-sliced camera originals.
  - Rich Timeline Markers: Color-coded markers for editing rules ([強制] -> Red, [一般] -> Blue) with full reason comments.
  - Multi-Track Audio Mapping: Synchronized master host audio tracks (CAM1) across the entire sequence timeline.
  - Safe URI Path Encoding: Robust path cleaning and URL-encoding (file://localhost/...) for cross-platform NLE relinking.

Usage Examples:
  # Example 1: Auto-discover full EDL in directory and export XML
  python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml

  # Example 2: Export specific EDL CSV file
  python3 scripts/export_fcp7_xml.py \
    -e edl_full.csv \
    -o final_cut_full.xml
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

# Support internal modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from modules.edl_validator import validate_edl_file, format_validation_report
except ImportError:
    from scripts.modules.edl_validator import validate_edl_file, format_validation_report


DEFAULT_FPS = 30
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
AUDIO_SAMPLE_RATE = 48000
AUDIO_DEPTH = 16


def resolve_fps_characteristics(fps):
    """
    Map float fps to FCP7 integer timebase and ntsc boolean flag according to Apple FCP7 XML specifications.
    Supports standard NTSC fractional frame rates (23.976, 29.97, 59.94) and broadcast film rates.
    """
    fps_f = float(fps)
    KNOWN_RATES = [
        (23.976, 24, True),
        (23.98,  24, True),
        (24.0,   24, False),
        (25.0,   25, False),
        (29.97,  30, True),
        (30.0,   30, False),
        (50.0,   50, False),
        (59.94,  60, True),
        (60.0,   60, False),
    ]
    for target, tb, is_ntsc in KNOWN_RATES:
        if abs(fps_f - target) < 0.01:
            return tb, is_ntsc

    tb = int(round(fps_f))
    sys.stderr.write(
        f"[Warning] Unrecognised frame rate {fps}. Setting FCP7 XML timebase={tb}, ntsc=FALSE.\n"
    )
    sys.stderr.flush()
    return tb, False


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


def format_path_for_xml(system_path):
    """Convert absolute path to URL-encoded file://localhost URI format for XML."""
    path_obj = Path(system_path)
    path_str = path_obj.as_posix()
    if not path_str.startswith("/"):
        path_str = "/" + path_str
    encoded_path = urllib.parse.quote(path_str, safe="/:")
    return f"file://localhost{encoded_path}"


def auto_discover_camera_files(media_dir):
    """
    Discover camera files in media_dir.
    Prioritizes full synchronized master files (*_synced.mp4), followed by camera video files.
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

    # 2. Fallback: any valid camera video files
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


def create_file_node(file_id, filename, file_url, timebase, duration, width, height, drop_frame=False):
    """Generate standard FCP7 XML <file> node with Reel name matching reference implementation."""
    reel_name = os.path.splitext(filename)[0]
    display_format = "DF" if drop_frame else "NDF"
    return f"""
                    <file id="{file_id}">
                        <name>{filename}</name>
                        <pathurl>{file_url}</pathurl>
                        <rate><timebase>{timebase}</timebase></rate>
                        <duration>{duration}</duration>
                        <timecode>
                            <rate><timebase>{timebase}</timebase></rate>
                            <string>00:00:00:00</string>
                            <frame>0</frame>
                            <displayformat>{display_format}</displayformat>
                            <reel>
                                <name>{reel_name}</name>
                            </reel>
                        </timecode>
                        <media>
                            <video>
                                <samplecharacteristics>
                                    <rate><timebase>{timebase}</timebase></rate>
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
                            fps=DEFAULT_FPS, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                            drop_frame=False):
    """
    Construct standard Final Cut Pro 7 XML (xmeml version 4) content matching working reference.
    """
    total_timeline_duration = all_part_clips[-1]["timeline_end"] if all_part_clips else 0
    timebase, is_ntsc = resolve_fps_characteristics(fps)
    ntsc_str = "TRUE" if is_ntsc else "FALSE"
    display_format = "DF" if drop_frame else "NDF"

    xml_header = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
<sequence id="sequence-1">
    <name>{seq_name}</name>
    <duration>{total_timeline_duration}</duration>
    <rate>
        <timebase>{timebase}</timebase>
        <ntsc>{ntsc_str}</ntsc>
    </rate>
    <timecode>
        <rate>
            <timebase>{timebase}</timebase>
            <ntsc>{ntsc_str}</ntsc>
        </rate>
        <string>00:00:00:00</string>
        <frame>0</frame>
        <displayformat>{display_format}</displayformat>
    </timecode>
    <media>
        <video>
            <format>
                <samplecharacteristics>
                    <rate><timebase>{timebase}</timebase></rate>
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

        lookup_key = cam_key
        if lookup_key not in file_id_map:
            file_id_map[lookup_key] = f"masterclip-{lookup_key}"

        master_file_id = file_id_map[lookup_key]
        clip_id = f"video-item-{i}"
        clip_dur = clip["source_out"] - clip["source_in"]

        file_node = create_file_node(master_file_id, filename, file_url, timebase, total_timeline_duration + 50000, width, height, drop_frame=drop_frame)

        marker_color = "(255,0,0)" if "[強制]" in clip["rule"] else "(0,0,255)"
        clean_rule = clip["rule"].replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        clean_reason = clip["reason"].replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

        xml_video_body += f"""
                <clipitem id="{clip_id}">
                    <name>{cam_key}</name>
                    <enabled>TRUE</enabled>
                    <duration>{clip_dur}</duration>
                    <rate><timebase>{timebase}</timebase></rate>
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

    # Audio Track Section: Master host audio (CAM1)
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
                a_master_id = "masterclip-CAM1-Audio"
                a_clip_id = f"audio-track{track_idx}-item{a_idx}"
                a_dur_frames = a_part["end_frame"] - a_part["start_frame"]

                a_file_node = create_file_node(a_master_id, a_fname, a_url, timebase, total_timeline_duration + 50000, width, height, drop_frame=drop_frame)

                tracks_xml += f"""
                <clipitem id="{a_clip_id}">
                    <name>CAM1 Audio</name>
                    <enabled>TRUE</enabled>
                    <duration>{a_dur_frames}</duration>
                    <rate><timebase>{timebase}</timebase></rate>
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
                             use_raw_media=False, drop_frame=False, strict_edl=False,
                             lang="en"):
    """
    Main pipeline to convert sequential EDL CSVs into a continuous FCP7 XML sequence.
    """
    if not edl_files:
        raise ValueError("No EDL CSV files provided for XML export.")

    fps = float(fps)
    timebase, is_ntsc = resolve_fps_characteristics(fps)
    if drop_frame and not is_ntsc:
        print(f"[Error] --drop-frame is only valid for NTSC frame rates (23.976, 29.97, 59.94). Specified rate: {fps}", file=sys.stderr)
        sys.exit(1)

    edl_files = sorted(edl_files, key=natural_sort_key)
    num_edls = len(edl_files)

    # Resolve output XML path
    if not output_path:
        first_dir = os.path.dirname(os.path.abspath(edl_files[0]))
        if num_edls == 1:
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
    print(f"🎬  FCP7 XML Exporter: Converting {num_edls} EDL File(s) to Final Cut Pro 7 XML")
    print("=" * 78)
    print(f"  • Target XML Output : {output_path}")
    print(f"  • Media Directory   : {media_dir}")
    print(f"  • Sequence Rate     : {fps} fps ({width}x{height})")
    print(f"  • Media Source Mode : {'Original Raw Camera Footage' if use_raw_media else 'Synchronized Camera Masters (*_synced.mp4)'}")
    print("-" * 78)

    # Auto-discover camera files in media directory
    cam_map = auto_discover_camera_files(media_dir)

    # Prepare raw camera map & global trim offsets if use_raw_media is requested
    raw_cam_map = {}
    raw_offset_map = {}
    if use_raw_media and sync_metadata:
        trim_meta = sync_metadata.get("trim") or {}
        ref_start_str = trim_meta.get("ref_start") or "0"
        t_ref_start_sec = time_str_to_frames(ref_start_str, fps) / fps if ref_start_str else 0.0

        cams_info = sync_metadata.get("cameras", [])
        for c_idx, c_info in enumerate(cams_info, start=1):
            c_key = f"CAM{c_idx}"
            c_name = c_info.get("camera", "")
            if c_info.get("is_ref"):
                c_offset = t_ref_start_sec
                raw_fpath = sync_metadata.get("ref_video") or c_name
            else:
                c_offset = t_ref_start_sec - c_info.get("offset_sec", 0.0)
                raw_fpath = c_name

            if not os.path.isabs(raw_fpath) and media_dir:
                candidate_fpath = os.path.join(media_dir, raw_fpath)
                if os.path.exists(candidate_fpath):
                    raw_fpath = candidate_fpath

            raw_cam_map[c_key] = os.path.abspath(raw_fpath)
            raw_offset_map[c_key] = c_offset

    all_timeline_clips = []
    accumulated_offset = 0

    for edl_idx, edl_path in enumerate(edl_files, start=1):
        edl_bname = os.path.basename(edl_path)
        known_cams = list(cam_map.keys()) if cam_map else None
        val_result = validate_edl_file(edl_path, known_cameras=known_cams, lang=lang)
        print(f"\n{format_validation_report(val_result, lang=lang)}")
        if val_result.has_error and strict_edl:
            print(f"\n[Error] EDL validation failed with errors for {edl_bname} under --strict-edl mode.", file=sys.stderr)
            sys.exit(1)

        records = load_edl_csv_records(edl_path)
        if not records:
            print(f"  [Warning] No valid records in {edl_bname}, skipping...")
            continue

        edl_clip_count = len(records)
        edl_max_out_frame = 0

        for rec in records:
            in_frame = time_str_to_frames(rec["start_str"], fps)
            out_frame = time_str_to_frames(rec["end_str"], fps)
            if out_frame > edl_max_out_frame:
                edl_max_out_frame = out_frame

            cam_name = rec["camera"].strip()
            cam_upper = cam_name.upper()

            # Resolve file path and frame points
            if use_raw_media and cam_upper in raw_offset_map:
                raw_start_sec = raw_offset_map[cam_upper]
                global_offset_frames = int(round(raw_start_sec * fps))
                source_in = global_offset_frames + in_frame
                source_out = global_offset_frames + out_frame
                fpath = raw_cam_map.get(cam_upper) or cam_map.get(cam_upper)
            else:
                fpath = cam_map.get(cam_upper) or cam_map.get(cam_name)
                source_in = in_frame
                source_out = out_frame

            all_timeline_clips.append({
                "camera": cam_name,
                "file_path": fpath,
                "source_in": source_in,
                "source_out": source_out,
                "timeline_start": accumulated_offset + in_frame,
                "timeline_end": accumulated_offset + out_frame,
                "rule": rec["rule"],
                "reason": rec["reason"]
            })

        edl_dur_sec = edl_max_out_frame / fps
        print(f"  [EDL {edl_idx}/{num_edls}] {edl_bname:<20} | {edl_clip_count} cuts | Duration: {edl_dur_sec:.2f}s")
        accumulated_offset += edl_max_out_frame

    # Construct single continuous master host audio track (CAM1)
    part_audio_list = []
    cam1_audio = cam_map.get("CAM1")
    if cam1_audio:
        part_audio_list.append({
            "audio_path": cam1_audio,
            "start_frame": 0,
            "end_frame": accumulated_offset,
            "source_in": 0
        })

    seq_name = os.path.splitext(os.path.basename(output_path))[0]
    xml_content = build_fcp7_xml_sequence(
        all_part_clips=all_timeline_clips,
        part_audio_list=part_audio_list,
        seq_name=seq_name,
        fps=fps,
        width=width,
        height=height,
        drop_frame=drop_frame
    )

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_content)

    total_sec = accumulated_offset / fps
    total_min = total_sec / 60.0

    print("\n" + "=" * 78)
    print("✅  FCP7 XML Export Completed Successfully!")
    print(f"  • Exported XML File: {output_path}")
    print(f"  • Total Cuts       : {len(all_timeline_clips)} clips across {num_edls} EDL(s)")
    print(f"  • Total Duration   : {int(total_sec // 60):02d}:{total_sec % 60:06.3f} ({total_sec:.2f}s / {total_min:.1f} mins)")
    print(f"  • Timeline Frames  : {accumulated_offset} frames @ {fps} fps")
    print("  ► Ready for import into DaVinci Resolve / Premiere Pro / Final Cut Pro!")
    print("=" * 78 + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="FCP7 XML Exporter CLI: Convert multi-camera EDL CSV files into Final Cut Pro 7 XML for DaVinci Resolve / Premiere Pro.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-e", "--edl", nargs="+", default=None, help="One or more EDL CSV files (e.g. edl_full.csv)")
    parser.add_argument("-d", "--dir", default=None, help="Directory containing EDL CSV files (auto-discovers edl_full.csv)")
    parser.add_argument("-o", "--output", default=None, help="Path to output FCP7 XML file (default: final_cut_full.xml)")
    parser.add_argument("-m", "--media-dir", default=None, help="Directory containing camera media files (defaults to EDL directory)")
    parser.add_argument("-s", "--sync-json", default=None, help="Path to multicam_sync.json for raw camera offset resolution")
    parser.add_argument("--use-raw-media", action="store_true", help="Link to original raw camera footage instead of synchronized camera masters")
    parser.add_argument("--strict-edl", action="store_true",
                        help="EDL 驗證出現 ERROR 時中斷執行（預設僅警告並繼續）")
    parser.add_argument("--lang", default="en",
                        help="Language for the EDL validation report (default: en)")

    parser.add_argument("--fps", type=float, default=float(DEFAULT_FPS), help="Sequence frame rate (default: 30)")
    parser.add_argument("--drop-frame", action="store_true", help="Enable drop-frame timecode (DF) for NTSC sequences (default: NDF)")
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

        # If a unified full-length EDL exists, prioritize it
        if edl_files:
            full_edls = [f for f in edl_files if "full" in os.path.basename(f).lower()]
            if full_edls:
                full_edls.sort(key=lambda x: (0 if os.path.basename(x) == "edl_full.csv" else 1, len(os.path.basename(x))))
                edl_files = [full_edls[0]]

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
            use_raw_media=args.use_raw_media,
            drop_frame=args.drop_frame,
            strict_edl=args.strict_edl,
            lang=args.lang
        )
    except Exception as e:
        print(f"\n[Error] FCP7 XML export failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
