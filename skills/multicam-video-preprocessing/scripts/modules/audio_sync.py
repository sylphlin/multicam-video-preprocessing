"""
Audio time alignment core module (Audio Sync Module).
Computes physical time offsets (delta t) and statistical confidence scores
between reference and target cameras using 1D FFT cross-correlation.
"""

import concurrent.futures
import os
import subprocess
import tempfile
import time
import wave
import numpy as np


def extract_audio_track(video_path, output_wav, sr=8000, max_duration=None):
    """
    Extract mono 16-bit PCM audio from a video file.
    """
    cmd = ["ffmpeg", "-y"]
    if max_duration:
        cmd.extend(["-t", str(max_duration)])
    cmd.extend([
        "-i", video_path,
        "-vn", "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le",
        output_wav
    ])
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        err_msg = res.stderr[-400:]
        raise RuntimeError(f"FFmpeg audio extraction failed ({os.path.basename(video_path)}): {err_msg}")


def load_and_preprocess_audio(wav_path):
    """
    Load a WAV file and apply preprocessing:
    1. Zero-mean DC offset removal.
    2. First-order high-pass difference pre-emphasis (reduces HVAC low rumble, enhances voice/clap transients).
    3. Z-score normalization (eliminates recording volume gain disparities).
    """
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        data = wf.readframes(n_frames)
        raw = np.frombuffer(data, dtype=np.int16).astype(np.float32)

    if len(raw) == 0:
        raise ValueError(f"Audio file is empty: {wav_path}")

    # 1. Zero-mean
    sig = raw - np.mean(raw)

    # 2. First-order high-pass difference
    if len(sig) > 1:
        sig_hp = np.diff(sig)
    else:
        sig_hp = sig

    # 3. Energy normalization
    std_val = np.std(sig_hp)
    if std_val > 1e-6:
        sig_norm = sig_hp / std_val
    else:
        sig_norm = sig_hp

    return sig_norm, sr, len(raw) / sr


def compute_cross_correlation(s_ref, s_target, sr):
    """
    Compute time offset and statistical significance confidence score using 1D FFT cross-correlation.
    """
    len_ref = len(s_ref)
    len_target = len(s_target)
    n = len_ref + len_target - 1
    fft_size = 1 << (n - 1).bit_length()

    f_ref = np.fft.rfft(s_ref, fft_size)
    f_target = np.fft.rfft(s_target, fft_size)
    corr = np.fft.irfft(f_ref * np.conj(f_target), fft_size)

    max_idx = int(np.argmax(corr))
    peak_val = float(corr[max_idx])

    # Time offset in seconds
    offset_samples = max_idx - fft_size if max_idx > (fft_size // 2) else max_idx
    offset_sec = offset_samples / sr

    # Statistical significance analysis (Peak Z-Score)
    corr_std = float(np.std(corr))
    corr_mean = float(np.mean(corr))
    peak_z_score = (peak_val - corr_mean) / (corr_std + 1e-8) if corr_std > 0 else 0.0

    # Confidence mapping
    if peak_z_score >= 25.0:
        confidence = min(99.9, 95.0 + (peak_z_score - 25.0) * 0.1)
    elif peak_z_score >= 15.0:
        confidence = 85.0 + (peak_z_score - 15.0) * 1.0
    elif peak_z_score >= 8.0:
        confidence = 70.0 + (peak_z_score - 8.0) * 2.1
    elif peak_z_score >= 4.0:
        confidence = 50.0 + (peak_z_score - 4.0) * 5.0
    else:
        confidence = max(0.0, peak_z_score * 12.5)

    return offset_sec, {
        "peak_z_score": peak_z_score,
        "confidence": confidence,
        "offset_samples": offset_samples
    }


def sync_single_target(ref_info, target_video, tmpdir, sr=8000, max_dur=None):
    """
    Synchronization task worker for a single target camera.
    """
    t0 = time.time()
    target_basename = os.path.basename(target_video)
    target_wav = os.path.join(tmpdir, f"target_{os.path.basename(target_video)}.wav")

    extract_audio_track(target_video, target_wav, sr=sr, max_duration=max_dur)
    t_extract = time.time()

    s_target, _, target_dur = load_and_preprocess_audio(target_wav)
    offset_sec, stats = compute_cross_correlation(ref_info["s_ref"], s_target, sr)
    t_calc = time.time()

    return {
        "target_video": target_video,
        "target_basename": target_basename,
        "duration_sec": target_dur,
        "offset_sec": offset_sec,
        "confidence": stats["confidence"],
        "peak_z_score": stats["peak_z_score"],
        "extract_time": t_extract - t0,
        "calc_time": t_calc - t_extract,
        "total_time": t_calc - t0,
    }


def sync_all_targets(ref_video, target_videos, sr=8000, sample_dur=None, workers=4):
    """
    Main orchestration function for multi-camera global time alignment.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Reference camera
        ref_wav = os.path.join(tmpdir, "ref_anchor.wav")
        extract_audio_track(ref_video, ref_wav, sr=sr, max_duration=sample_dur)
        s_ref, _, ref_dur = load_and_preprocess_audio(ref_wav)

        ref_info = {
            "s_ref": s_ref,
            "duration_sec": ref_dur,
            "path": ref_video,
            "basename": os.path.basename(ref_video),
            "sr": sr
        }

        # 2. Parallel processing for all target cameras
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(sync_single_target, ref_info, tgt, tmpdir, sr, sample_dur): tgt
                for tgt in target_videos
            }
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                results.append(res)

    results.sort(key=lambda r: target_videos.index(r["target_video"]))
    return ref_info, results
