"""
Chapter segmentation and video cutting module (Video Segmenter Module).
Integrates CAM1 natural silence/pause detection with multi-camera stream-copy slicing.
"""

import concurrent.futures
import os
import re
import subprocess
import tempfile
import time
import wave
import numpy as np

from .time_utils import format_seconds
from .audio_normalizer import build_loudnorm_filter


# ---------------------------------------------------------------------------
# 1. Silence Detection & Natural Pause Analysis
# ---------------------------------------------------------------------------

def detect_all_silences(video_or_audio_path, noise_threshold="-30dB", min_duration=0.5, start_sec=None, dur_sec=None):
    """
    Scan audio track for silence intervals using FFmpeg silencedetect.
    Supports fast seeking to a candidate window (start_sec, dur_sec) to avoid scanning full file.
    """
    cmd = ["ffmpeg"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", str(start_sec)])
    if dur_sec is not None and dur_sec > 0:
        cmd.extend(["-t", str(dur_sec)])

    cmd.extend([
        "-i", video_or_audio_path,
        "-vn", "-sn", "-dn",
        "-af", f"silencedetect=noise={noise_threshold}:d={min_duration}",
        "-f", "null", "-"
    ])
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    output = res.stderr

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
            "mid": (start + end) / 2.0
        })
    return intervals


def find_rms_energy_minimum(audio_path, start_sec, end_sec, win_sec=0.5):
    """
    Fallback: scan for the lowest RMS energy window (breathing pause) if no silence is detected.
    """
    dur_sec = end_sec - start_sec
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "window.wav")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec), "-t", str(dur_sec),
            "-i", audio_path,
            "-vn", "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path
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
            chunk = data[i * win_samples: (i + 1) * win_samples]
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms < min_rms:
                min_rms = rms
                best_time = start_sec + (i + 0.5) * win_sec

        return best_time


def compute_common_overlap_range(ref_info, target_results):
    """
    Calculate the overlapping time range [overlap_start, overlap_end] relative to reference camera timeline
    where ALL cameras are simultaneously active.
    """
    overlap_start = 0.0
    overlap_end = ref_info["duration_sec"]

    for tgt in target_results:
        off = tgt["offset_sec"]
        tgt_dur = tgt["duration_sec"]
        # In ref timeline, target camera exists in [off, off + tgt_dur]
        overlap_start = max(overlap_start, off)
        overlap_end = min(overlap_end, off + tgt_dur)

    return overlap_start, max(overlap_start, overlap_end)


def find_natural_split_points(ref_path, start_sec=0.0, end_sec=None, total_duration=None, min_dur_sec=1800, max_dur_sec=2400):
    """
    Identify natural pause split points within target 30-40 min windows across the valid [start_sec, end_sec] interval.
    Automatically balances part counts to avoid residual short fragments.
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
        win_start = max(curr_pos + min_dur_sec * 0.7, nominal_cut - 300)
        win_end = min(nominal_cut + 300, end_sec - (min_dur_sec * 0.7))
        win_dur = max(1.0, win_end - win_start)

        # Fast windowed silence detection (scans only candidate window, not full file)
        cands = detect_all_silences(ref_path, noise_threshold="-30dB", min_duration=0.5, start_sec=win_start, dur_sec=win_dur)

        if cands:
            cands.sort(key=lambda s: abs(s["mid"] - nominal_cut) - min(s["duration"], 3.0) * 15)
            best_cut = cands[0]["mid"]
        else:
            best_cut = find_rms_energy_minimum(ref_path, win_start, win_end)

        split_points.append(best_cut)
        curr_pos = best_cut

    split_points.append(end_sec)
    return split_points


def build_part_segments(split_points, ref_info, target_results, audio_map=None):
    """
    Build structured part segment objects with time offsets and audio paths.
    Guarantees exact identical duration across all cameras for each part.
    """
    parts = []
    num_parts = len(split_points) - 1

    ref_audio = audio_map.get(ref_info["path"]) if audio_map else None

    for i in range(num_parts):
        p_ref_start = split_points[i]
        p_ref_end = split_points[i + 1]
        part_dur = p_ref_end - p_ref_start

        part_item = {
            "part_index": i + 1,
            "part_name": f"part{i + 1}",
            "duration_sec": part_dur,
            "ref_start": p_ref_start,
            "ref_end": p_ref_end,
            "cameras": []
        }

        part_item["cameras"].append({
            "camera_name": ref_info["basename"],
            "camera_path": ref_info["path"],
            "audio_path": ref_audio,
            "is_ref": True,
            "start_sec": p_ref_start,
            "end_sec": p_ref_end,
            "offset_sec": 0.0
        })

        for tgt in target_results:
            off = tgt["offset_sec"]
            tgt_start = p_ref_start - off
            tgt_end = p_ref_end - off
            tgt_audio = audio_map.get(tgt["target_video"]) if audio_map else None

            part_item["cameras"].append({
                "camera_name": tgt["target_basename"],
                "camera_path": tgt["target_video"],
                "audio_path": tgt_audio,
                "is_ref": False,
                "start_sec": tgt_start,
                "end_sec": tgt_end,
                "offset_sec": off
            })

        parts.append(part_item)

    return parts


# ---------------------------------------------------------------------------
# 2. Video Cutting and Export
# ---------------------------------------------------------------------------

def cut_single_clip(video_path, output_path, start_sec, end_sec,
                    norm_audio_path=None, copy_codec=True,
                    video_bitrate="6000k", audio_bitrate="192k"):
    """
    Cut video sub-clip using lossless stream-copy (-c copy):
    - If norm_audio_path is provided: mux original video stream with normalized audio stream in one step.
    - If norm_audio_path is not provided: stream-copy directly from original video.
    """
    if start_sec < 0:
        start_sec = 0.0
    dur_sec = max(0.0, end_sec - start_sec)

    if norm_audio_path and os.path.exists(norm_audio_path):
        cmd = [
            "ffmpeg", "-y",
            "-ss", format_seconds(start_sec),
            "-i", video_path,
            "-ss", format_seconds(start_sec),
            "-i", norm_audio_path,
            "-t", format_seconds(dur_sec),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c", "copy",
            output_path
        ]
    elif copy_codec:
        cmd = [
            "ffmpeg", "-y",
            "-ss", format_seconds(start_sec),
            "-i", video_path,
            "-t", format_seconds(dur_sec),
            "-c", "copy",
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", format_seconds(start_sec),
            "-i", video_path,
            "-t", format_seconds(dur_sec),
            "-c:v", "h264_videotoolbox", "-b:v", video_bitrate,
            "-c:a", "aac", "-b:a", audio_bitrate,
            output_path
        ]

    t0 = time.time()
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        err_msg = res.stderr[-600:]
        raise RuntimeError(f"FFmpeg video cutting failed ({os.path.basename(video_path)}): {err_msg}")
    return time.time() - t0


def _cut_task_wrapper(task_args):
    """
    Worker wrapper for parallel cutting execution.
    """
    cam_name, v_path, a_path, out_path, s_sec, e_sec, copy_c, v_bit, a_bit = task_args
    mode_tag = "Stream Copy + Norm Audio" if a_path else "Stream Copy (-c copy)"
    print(f"    ► Slicing {cam_name} ({format_seconds(s_sec)} → {format_seconds(e_sec)}) [{mode_tag}] → {os.path.basename(out_path)} ...")
    t_proc = cut_single_clip(
        v_path, out_path, s_sec, e_sec,
        norm_audio_path=a_path, copy_codec=copy_c,
        video_bitrate=v_bit, audio_bitrate=a_bit
    )
    print(f"      ✓ Finished {os.path.basename(out_path)} in {t_proc:.1f}s")
    return t_proc


def cut_all_split_parts(part_segments, output_dir, copy_codec=True,
                        video_bitrate="6000k", audio_bitrate="192k",
                        workers=2):
    """
    Batch cut all sub-clips in parallel across all cameras and parts.
    """
    t_start = time.time()
    task_list = []

    for part in part_segments:
        part_name = part["part_name"]

        for cam in part["cameras"]:
            base, ext = os.path.splitext(cam["camera_name"])
            out_filename = f"{base}_{part_name}{ext}"
            out_path = os.path.join(output_dir, out_filename)

            task_list.append((
                cam["camera_name"], cam["camera_path"], cam.get("audio_path"), out_path,
                cam["start_sec"], cam["end_sec"],
                copy_codec,
                video_bitrate, audio_bitrate
            ))

    print(f"\n  [SLICING] Exporting {len(task_list)} sub-clips ({workers} parallel workers)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_cut_task_wrapper, task) for task in task_list]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()

    return len(task_list), time.time() - t_start
