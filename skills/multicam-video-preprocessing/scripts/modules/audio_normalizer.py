"""
Audio Loudness Normalizer Module.
Complies with EBU R128 / ITU-R BS.1770 broadcast and YouTube recommended standards (-14.0 LUFS).
Extracts and normalizes individual audio tracks (~50MB each) independently to conserve disk space.
"""

import concurrent.futures
import os
import subprocess
import time


def build_loudnorm_filter(lufs=-14.0, lra=11.0, tp=-1.5):
    """
    Construct FFmpeg loudnorm filter argument string.
    - I: Integrated Loudness (default -14.0 LUFS)
    - LRA: Loudness Range (default 11.0 LU)
    - TP: Maximum True Peak (default -1.5 dBTP)
    """
    return f"loudnorm=I={lufs}:LRA={lra}:TP={tp}"


def normalize_single_audio_track(input_video, output_audio_m4a, lufs=-14.0, lra=11.0, tp=-1.5,
                                audio_bitrate="192k"):
    """
    Extract audio track from video and normalize to EBU R128 (-14 LUFS) as AAC .m4a.
    """
    af = build_loudnorm_filter(lufs=lufs, lra=lra, tp=tp)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vn",
        "-af", af,
        "-c:a", "aac", "-b:a", audio_bitrate,
        output_audio_m4a
    ]
    t0 = time.time()
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        err_msg = res.stderr[-600:]
        raise RuntimeError(f"Audio loudness normalization failed ({os.path.basename(input_video)}): {err_msg}")
    return time.time() - t0


def normalize_all_audio_tracks(all_videos, tmpdir, lufs=-14.0, lra=11.0, tp=-1.5,
                               audio_bitrate="192k", workers=2):
    """
    Normalize audio tracks for all camera sources in parallel.
    """
    t0 = time.time()
    audio_map = {}

    for v in all_videos:
        base, _ = os.path.splitext(os.path.basename(v))
        out_a = os.path.join(tmpdir, f"{base}_norm.m4a")
        audio_map[v] = out_a

    print(f"  [LOUDNORM] Normalizing {len(all_videos)} audio tracks to EBU R128 ({lufs} LUFS)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(normalize_single_audio_track, in_p, out_p, lufs, lra, tp, audio_bitrate): in_p
            for in_p, out_p in audio_map.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            in_p = futures[fut]
            t_sec = fut.result()
            print(f"    ✓ {os.path.basename(in_p)} audio normalization completed in {t_sec:.1f}s")

    total_time = time.time() - t0
    return audio_map, total_time
