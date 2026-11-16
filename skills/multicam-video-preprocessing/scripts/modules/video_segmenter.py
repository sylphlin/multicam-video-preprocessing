"""
Silence-aware chapter segmentation and lossless stream-copy slicing module.
Detects natural speech pauses in target time windows and slices temporary video chunks.
"""

import csv
import io
import os
import re
import subprocess
import tempfile
import wave
import numpy as np

try:
    from .edl_validator import parse_edl_time_to_seconds
    from .time_utils import format_seconds, ensure_ffmpeg_in_path
except ImportError:
    from modules.edl_validator import parse_edl_time_to_seconds
    from modules.time_utils import format_seconds, ensure_ffmpeg_in_path


def _safe_parse_time(val):
    """Parse EDL timestamp string to float seconds, or return None if invalid."""
    try:
        if val is None or not str(val).strip():
            return None
        return parse_edl_time_to_seconds(val)
    except Exception:
        return None



def format_edl_timestamp(seconds: float) -> str:
    """
    Format seconds into MM:SS.mmm (under 1 hour) or HH:MM:SS.mmm (1 hour or more).
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    if total_min < 60:
        return f"{total_min:02d}:{s:02d}.{ms:03d}"
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def detect_all_silences(
    video_or_audio_path: str,
    noise_threshold: str = "-30dB",
    min_duration: float = 0.5,
    start_sec: float = None,
    dur_sec: float = None,
) -> list:
    """
    Scan audio track for silence intervals using FFmpeg silencedetect.
    Seek to candidate window (start_sec, dur_sec) to avoid full-file scan.
    """
    ensure_ffmpeg_in_path()
    cmd = ["ffmpeg"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", str(start_sec)])
    if dur_sec is not None and dur_sec > 0:
        cmd.extend(["-t", str(dur_sec)])

    cmd.extend([
        "-i", video_or_audio_path,
        "-vn", "-sn", "-dn",
        "-af", f"silencedetect=noise={noise_threshold}:d={min_duration}",
        "-f", "null", "-",
    ])
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    output = res.stderr or ""

    time_offset = start_sec if (start_sec is not None and start_sec > 0) else 0.0
    silence_starts = []
    silence_ends = []
    silence_durations = []

    for line in output.splitlines():
        if "silence_start:" in line:
            m = re.search(r"silence_start:\s*([0-9\.]+)", line)
            if m:
                silence_starts.append(float(m.group(1)) + time_offset)
        elif "silence_end:" in line:
            m = re.search(r"silence_end:\s*([0-9\.]+)\s*\|\s*silence_duration:\s*([0-9\.]+)", line)
            if m:
                silence_ends.append(float(m.group(1)) + time_offset)
                silence_durations.append(float(m.group(2)))

    intervals = []
    for start, end, dur in zip(silence_starts, silence_ends, silence_durations):
        intervals.append({
            "start": start,
            "end": end,
            "duration": dur,
            "mid": (start + end) / 2.0,
        })
    return intervals


def find_rms_energy_minimum(
    audio_path: str,
    start_sec: float,
    end_sec: float,
    win_sec: float = 0.5,
) -> float:
    """
    Scan candidate window for the lowest RMS audio energy point when no silence is detected.
    """
    ensure_ffmpeg_in_path()
    dur_sec = max(1.0, end_sec - start_sec)
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "window.wav")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-t", str(dur_sec),
            "-i", audio_path,
            "-vn", "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(wav_path):
            return (start_sec + end_sec) / 2.0

        with wave.open(wav_path, "rb") as wf:
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)

        if len(data) == 0:
            return (start_sec + end_sec) / 2.0

        win_samples = int(win_sec * 8000)
        n_win = len(data) // win_samples
        if n_win == 0:
            return (start_sec + end_sec) / 2.0

        min_rms = float("inf")
        best_time = (start_sec + end_sec) / 2.0

        for i in range(n_win):
            chunk = data[i * win_samples : (i + 1) * win_samples]
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms < min_rms:
                min_rms = rms
                best_time = start_sec + (i + 0.5) * win_sec

        return best_time


def find_natural_split_points(
    video_path: str,
    start_sec: float = 0.0,
    end_sec: float = None,
    total_duration: float = None,
    min_dur_sec: float = 1800.0,
    max_dur_sec: float = 2400.0,
) -> list:
    """
    Identify natural pause split points within target 30-40 minute windows.
    Balance part counts automatically to avoid short trailing fragments.
    """
    if end_sec is None:
        end_sec = total_duration if total_duration is not None else 0.0

    valid_duration = end_sec - start_sec
    if valid_duration <= max_dur_sec:
        return [start_sec, end_sec]

    target_dur = (min_dur_sec + max_dur_sec) / 2.0
    num_parts = max(1, round(valid_duration / target_dur))
    if num_parts == 1 and valid_duration > max_dur_sec:
        num_parts = 2

    split_points = [start_sec]
    curr_pos = start_sec

    for p_idx in range(1, num_parts):
        nominal_cut = start_sec + (valid_duration / num_parts) * p_idx
        win_start = max(curr_pos + min_dur_sec * 0.7, nominal_cut - 300.0)
        win_end = min(nominal_cut + 300.0, end_sec - (min_dur_sec * 0.7))
        win_dur = max(1.0, win_end - win_start)

        cands = detect_all_silences(
            video_path,
            noise_threshold="-30dB",
            min_duration=0.5,
            start_sec=win_start,
            dur_sec=win_dur,
        )

        if cands:
            cands.sort(key=lambda s: abs(s["mid"] - nominal_cut) - min(s["duration"], 3.0) * 15.0)
            best_cut = cands[0]["mid"]
        else:
            best_cut = find_rms_energy_minimum(video_path, win_start, win_end)

        # Round split point to 1 decimal place to align cleanly with 10 fps / short GOP boundaries
        best_cut = round(best_cut, 1)
        split_points.append(best_cut)
        curr_pos = best_cut

    split_points.append(end_sec)
    return split_points


def slice_video_into_temp_chunks(
    video_path: str,
    split_points: list,
    chunks_dir: str,
) -> list:
    """
    Slice full grid video at split_points into chunks_dir using lossless stream copy (-c copy).
    Return list of chunk metadata dicts.
    """
    ensure_ffmpeg_in_path()
    os.makedirs(chunks_dir, exist_ok=True)
    chunks = []
    num_parts = len(split_points) - 1

    for i in range(num_parts):
        start_s = float(split_points[i])
        end_s = float(split_points[i + 1])
        dur_s = max(0.1, end_s - start_s)
        part_name = f"part{i + 1}"
        out_path = os.path.join(chunks_dir, f"multicam_merged_{part_name}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-ss", format_seconds(start_s),
            "-i", video_path,
            "-t", format_seconds(dur_s),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            out_path,
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(f"Failed to slice chunk {part_name}: {res.stderr}")

        chunks.append({
            "part_index": i + 1,
            "total_parts": num_parts,
            "part_name": part_name,
            "path": out_path,
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": dur_s,
        })

    return chunks


def shift_and_merge_chunk_edl_rows(chunk_results: list) -> list:
    """
    Shift relative timestamps in each chunk by chunk['start_sec'] and merge into one list of CSV rows.
    Stitch micro-gaps or overlaps at split boundaries to ensure monotonic contiguous cuts.
    """
    merged_header = ["Start_Time", "End_Time", "Best_Camera", "剪輯規則", "剪輯原因"]
    merged_rows = []

    for c_idx, item in enumerate(chunk_results):
        offset_sec = float(item.get("start_sec", 0.0))
        chunk_end_sec = float(item.get("end_sec", 0.0))
        rows = item.get("csv_rows")
        if not rows:
            csv_text = (item.get("csv_block") or "").strip()
            if csv_text:
                reader = csv.reader(io.StringIO(csv_text))
                rows = [r for r in reader if r and any(cell.strip() for cell in r)]
        if not rows:
            continue

        # Detect header row
        first_row_lower = " ".join(rows[0]).lower()
        has_header = any(k in first_row_lower for k in ("start", "end", "camera", "cam"))
        if has_header:
            if c_idx == 0 and len(rows[0]) >= 3:
                merged_header = rows[0]
            data_rows = rows[1:]
        else:
            data_rows = rows

        if not data_rows:
            continue

        # Check if timestamps in this chunk are relative (starting near 0) or already absolute
        first_start = None
        for r in data_rows:
            if len(r) >= 2:
                first_start = _safe_parse_time(r[0])
                if first_start is not None:
                    break

        is_relative = True
        if offset_sec > 60.0 and first_start is not None and first_start >= (offset_sec - 60.0):
            is_relative = False

        part_parsed_rows = []
        for r in data_rows:
            if len(r) < 3:
                continue
            s_val = _safe_parse_time(r[0])
            e_val = _safe_parse_time(r[1])
            if s_val is None or e_val is None:
                continue

            if is_relative:
                s_val += offset_sec
                e_val += offset_sec

            if chunk_end_sec > 0 and e_val > chunk_end_sec + 5.0:
                e_val = chunk_end_sec
            if e_val <= s_val:
                continue

            part_parsed_rows.append({
                "start": s_val,
                "end": e_val,
                "camera": r[2].strip(),
                "rule": r[3].strip() if len(r) > 3 else "",
                "reason": r[4].strip() if len(r) > 4 else "",
            })

        if not part_parsed_rows:
            continue

        # Stitch boundary between previous chunk and current chunk
        if merged_rows:
            prev_last = merged_rows[-1]
            curr_first = part_parsed_rows[0]
            boundary_gap = curr_first["start"] - prev_last["end"]
            if boundary_gap < 0:
                # Resolve overlap at split point
                boundary_mid = max(prev_last["start"] + 0.5, min(offset_sec, curr_first["end"] - 0.5))
                prev_last["end"] = boundary_mid
                curr_first["start"] = boundary_mid
            elif boundary_gap > 0.0:
                # Close gap across split boundary
                prev_last["end"] = curr_first["start"]

        merged_rows.extend(part_parsed_rows)

    result_rows = [merged_header]
    for row in merged_rows:
        result_rows.append([
            format_edl_timestamp(row["start"]),
            format_edl_timestamp(row["end"]),
            row["camera"],
            row["rule"],
            row["reason"],
        ])
    return result_rows

