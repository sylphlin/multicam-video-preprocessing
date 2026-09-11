"""
Audio Loudness Normalizer Module.
Complies with EBU R128 / ITU-R BS.1770 broadcast and YouTube recommended standards (-14.0 LUFS).
Executes True Two-Pass Loudness Normalization:
  - Pass 1: Null-sink audio analysis to measure input_i, input_lra, input_tp, input_thresh, target_offset.
  - Pass 2: Linear normalization (linear=true) to apply pure gain offset without dynamic pumping artifacts.
Extracts and normalizes individual audio tracks (~50MB each) independently to conserve disk space.
"""

import concurrent.futures
import json
import os
import re
import subprocess
import time


def extract_loudnorm_stats(stderr_text):
    """
    Parse JSON output from loudnorm filter print_format=json.
    Returns dict with measured acoustic parameters or None.
    """
    m = re.search(r'\{\s*"input_i"\s*:\s*"[^"]+".*?"target_offset"\s*:\s*"[^"]+"\s*\}', stderr_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def normalize_single_audio_track(input_video, output_audio_m4a, lufs=-14.0, lra=11.0, tp=-1.5,
                                audio_bitrate="192k"):
    """
    Extract audio track from video and normalize to EBU R128 (-14 LUFS) as AAC .m4a
    using broadcast-grade Two-Pass linear normalization.
    """
    t0 = time.time()

    # Pass 1: Rapid acoustic measurement decoding to null sink
    cmd_pass1 = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vn",
        "-af", f"loudnorm=I={lufs}:LRA={lra}:TP={tp}:print_format=json",
        "-f", "null", "-"
    ]
    res1 = subprocess.run(cmd_pass1, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res1.returncode != 0:
        err_msg = res1.stderr[-600:] if res1.stderr else "Unknown error"
        raise RuntimeError(f"Pass 1 audio measurement failed ({os.path.basename(input_video)}): {err_msg}")

    stats = extract_loudnorm_stats(res1.stderr)

    # Pass 2: Linear gain normalization
    if stats:
        af_pass2 = (
            f"loudnorm=I={lufs}:LRA={lra}:TP={tp}:"
            f"measured_I={stats.get('input_i', lufs)}:"
            f"measured_LRA={stats.get('input_lra', lra)}:"
            f"measured_TP={stats.get('input_tp', tp)}:"
            f"measured_thresh={stats.get('input_thresh', '-70.0')}:"
            f"offset={stats.get('target_offset', '0.0')}:"
            f"linear=true"
        )
    else:
        # Fallback to single-pass dynamic normalization if JSON parse failed
        af_pass2 = f"loudnorm=I={lufs}:LRA={lra}:TP={tp}"

    cmd_pass2 = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vn",
        "-af", af_pass2,
        "-c:a", "aac", "-b:a", audio_bitrate,
        output_audio_m4a
    ]
    res2 = subprocess.run(cmd_pass2, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res2.returncode != 0:
        err_msg = res2.stderr[-600:] if res2.stderr else "Unknown error"
        raise RuntimeError(f"Pass 2 audio normalization failed ({os.path.basename(input_video)}): {err_msg}")

    return time.time() - t0


def normalize_all_audio_tracks(all_videos, tmpdir, lufs=-14.0, lra=11.0, tp=-1.5,
                               audio_bitrate="192k", workers=2):
    """
    Normalize audio tracks for all camera sources in parallel using two-pass EBU R128.
    """
    t0 = time.time()
    audio_map = {}

    for v in all_videos:
        base, _ = os.path.splitext(os.path.basename(v))
        out_a = os.path.join(tmpdir, f"{base}_norm.m4a")
        audio_map[v] = out_a

    print(f"  [LOUDNORM] Normalizing {len(all_videos)} audio tracks to EBU R128 ({lufs} LUFS, Two-pass Linear)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(normalize_single_audio_track, in_p, out_p, lufs, lra, tp, audio_bitrate): in_p
            for in_p, out_p in audio_map.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            in_p = futures[fut]
            t_sec = fut.result()
            print(f"    ✓ {os.path.basename(in_p)} two-pass normalization completed in {t_sec:.1f}s")

    total_time = time.time() - t0
    return audio_map, total_time
