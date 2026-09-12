"""
Audio time alignment core module (Audio Sync Module).
Computes physical time offsets (delta t) and statistical confidence scores
between reference and target cameras using MFCC cross-correlation and sub-frame refinement.

Architecture:
1. MFCC-based acoustic correlation (100% pure numpy, zero external dependencies).
2. 3-tier fallback ladder:
   - Tier 1: Fast MFCC scan on the first 120s of target audio.
   - Tier 2: Full-length MFCC scan if Tier 1 score < 12.0 (or if --full-scan).
   - Tier 3: Full-length raw waveform 1D FFT cross-correlation fallback if MFCC score < 7.0.
3. Sub-frame acoustic refinement down to 0.125ms (single-sample precision at 8kHz).
4. BBC standard score confidence thresholds (High >= 12.0, Medium >= 7.0, Low < 7.0).
5. ffprobe container duration probing to guarantee correct timeline boundaries when --sample-dur is used.
"""

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
import numpy as np

# BBC standard confidence thresholds (Single Source of Truth)
SCORE_HIGH = 12.0  # BBC High confidence threshold (Fast ladder qualification)
SCORE_LOW = 7.0   # BBC Medium/Low boundary (Fallback to raw waveform & low warning)



def probe_media_duration(media_path):
    """
    Query the true total duration of a media file in seconds using ffprobe.
    """
    cmd_fmt = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path
    ]
    try:
        res = subprocess.run(cmd_fmt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = float(res.stdout.strip())
        if val > 0:
            return val
    except Exception:
        pass

    # Fallback to video stream duration if container format duration is absent
    cmd_stream = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path
    ]
    try:
        res = subprocess.run(cmd_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = float(res.stdout.strip())
        if val > 0:
            return val
    except Exception:
        pass

    return None


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


def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def mel_filterbank(sr, n_fft=256, n_mels=26, fmin=0.0, fmax=None):
    """
    Construct a triangular Mel-frequency filterbank matrix (pure numpy).
    """
    if fmax is None:
        fmax = sr / 2.0
    mel_min = _hz_to_mel(fmin)
    mel_max = _hz_to_mel(fmax)
    mel_pts = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    bin_pts = np.floor((n_fft + 1) * hz_pts / sr).astype(int)

    n_freqs = n_fft // 2 + 1
    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for i in range(1, n_mels + 1):
        l, c, r = bin_pts[i - 1], bin_pts[i], bin_pts[i + 1]
        if c == l:
            c = l + 1
        if r == c:
            r = c + 1
        r = min(r, fb.shape[1] - 1)
        c = min(c, r)
        if c > l:
            fb[i - 1, l:c] = (np.arange(l, c) - l) / (c - l)
        if r > c:
            fb[i - 1, c:r] = (r - np.arange(c, r)) / (r - c)
    return fb


def dct2_matrix(n_out=13, n_in=26):
    """
    Discrete Cosine Transform (DCT-II) matrix (pure numpy).
    """
    n = np.arange(n_in)
    k = np.arange(n_out).reshape(-1, 1)
    return np.cos(np.pi * k * (2 * n + 1) / (2 * n_in)).astype(np.float32)


def compute_mfcc(sig, sr=8000, n_mfcc=13, n_fft=256, win_length=256, hop_length=128, n_mels=26, fbank=None, dct_mat=None):
    """
    Compute Mel-frequency cepstral coefficients (MFCCs) with Cepstral Mean & Variance Normalization (CMVN).
    Drops coefficient c0 to remain invariant to microphone gain and absolute volume disparities.
    """
    sig = np.ascontiguousarray(sig, dtype=np.float32)
    if len(sig) < win_length:
        sig = np.pad(sig, (0, win_length - len(sig)))

    if fbank is None:
        fbank = mel_filterbank(sr, n_fft, n_mels)
    if dct_mat is None:
        dct_mat = dct2_matrix(n_mfcc, n_mels)

    num_frames = 1 + (len(sig) - win_length) // hop_length
    shape = (num_frames, win_length)
    strides = (sig.strides[0] * hop_length, sig.strides[0])
    frames = np.lib.stride_tricks.as_strided(sig, shape=shape, strides=strides)

    windowed = frames * np.hamming(win_length).astype(np.float32)
    spec = np.abs(np.fft.rfft(windowed, n=n_fft, axis=1)) ** 2
    mel_energies = np.dot(spec, fbank.T)
    log_mel = np.log(np.maximum(mel_energies, 1e-10))
    mfcc = np.dot(dct_mat, log_mel.T).astype(np.float32)

    # Exclude c0 (energy coefficient)
    mfcc = mfcc[1:n_mfcc]

    # Cepstral Mean & Variance Normalization (CMVN) along time axis
    mean = np.mean(mfcc, axis=1, keepdims=True)
    std = np.std(mfcc, axis=1, keepdims=True)
    return (mfcc - mean) / np.where(std > 1e-6, std, 1.0)


def refine_offset_subframe(sig_ref, sig_tgt, coarse_offset_sec, sr=8000, search_radius=256, win_dur=5.0):
    """
    Sub-frame sample-level physical acoustic refinement:
    Localizes a high-energy window in sig_ref within mutual active span and correlates against sig_tgt
    over +/- search_radius samples (+/- 32ms at 8kHz).
    Pushes precision from 16ms (MFCC hop) down to 0.125ms (single audio sample).
    """
    coarse_offset_samples = int(round(coarse_offset_sec * sr))
    win_len = int(win_dur * sr)
    common_start = max(0, coarse_offset_samples)
    common_end = min(len(sig_ref), coarse_offset_samples + len(sig_tgt))
    available_span = common_end - common_start
    min_required = win_len + 2 * search_radius

    if available_span < min_required:
        win_len = max(sr // 2, available_span - 2 * search_radius)
        if win_len <= 0:
            return coarse_offset_sec

    valid_ref_span = common_end - win_len - common_start
    if valid_ref_span <= 0:
        return coarse_offset_sec

    step = max(1, int(0.25 * sr))
    probe_starts = np.arange(common_start, common_end - win_len, step)
    if len(probe_starts) == 0:
        return coarse_offset_sec

    cumsum_sq = np.concatenate([[0.0], np.cumsum(sig_ref ** 2, dtype=np.float64)])
    e_starts = probe_starts
    e_ends = probe_starts + win_len
    energies = cumsum_sq[e_ends] - cumsum_sq[e_starts]
    best_ref_start = int(probe_starts[np.argmax(energies)])

    ref_slice = sig_ref[best_ref_start : best_ref_start + win_len]
    nominal_tgt_start = best_ref_start - coarse_offset_samples
    tgt_search_start = nominal_tgt_start - search_radius
    tgt_search_end = nominal_tgt_start + win_len + search_radius

    if tgt_search_start < 0 or tgt_search_end > len(sig_tgt):
        return coarse_offset_sec

    tgt_big = sig_tgt[tgt_search_start : tgt_search_end]
    corr = np.correlate(tgt_big, ref_slice, mode="valid")
    if len(corr) == 0 or np.max(corr) <= 0:
        return coarse_offset_sec

    best_idx = int(np.argmax(corr))
    actual_tgt_start = tgt_search_start + best_idx
    fine_offset_samples = best_ref_start - actual_tgt_start
    return fine_offset_samples / sr


def compute_mfcc_cross_correlation(m_ref, m_target, hop_length=128, sr=8000, f_ref_cache=None, lock=None):
    """
    Compute time offset and BBC standard score confidence using MFCC cross-correlation.
    """
    len_ref = m_ref.shape[1]
    len_tgt = m_target.shape[1]
    n = len_ref + len_tgt - 1
    fft_size = 1 << (n - 1).bit_length()

    f_ref = None
    if f_ref_cache is not None:
        if lock is not None:
            with lock:
                f_ref = f_ref_cache.get(fft_size)
        else:
            f_ref = f_ref_cache.get(fft_size)

    if f_ref is None:
        f_ref = np.fft.rfft(m_ref, fft_size, axis=1)
        if f_ref_cache is not None:
            if lock is not None:
                with lock:
                    f_ref_cache[fft_size] = f_ref
            else:
                f_ref_cache[fft_size] = f_ref

    f_tgt = np.fft.rfft(m_target, fft_size, axis=1)
    cross = np.sum(f_ref * np.conj(f_tgt), axis=0)
    corr = np.fft.irfft(cross, fft_size)

    max_idx = int(np.argmax(corr))
    peak_val = float(corr[max_idx])

    lag_frames = max_idx - fft_size if max_idx > (fft_size // 2) else max_idx
    offset_sec = (lag_frames * hop_length) / sr

    # Noise floor statistics: mask peak neighbourhood (+/- 15 frames)
    mask = np.ones(len(corr), dtype=bool)
    w = 15
    mask[max(0, max_idx - w):min(len(corr), max_idx + w + 1)] = False
    if max_idx < w:
        mask[-(w - max_idx):] = False
    elif max_idx > len(corr) - 1 - w:
        mask[:w - (len(corr) - 1 - max_idx)] = False

    noise_corr = corr[mask] if np.any(mask) else corr
    corr_std = float(np.std(noise_corr))
    corr_mean = float(np.mean(noise_corr))
    peak_z_score = (peak_val - corr_mean) / (corr_std + 1e-8) if corr_std > 0 else 0.0

    # BBC standard confidence mapping (Z >= 12.0 High, 7.0-12.0 Medium, < 7.0 Low)
    if peak_z_score >= 25.0:
        confidence = min(99.9, 98.0 + (peak_z_score - 25.0) * 0.05)
    elif peak_z_score >= 12.0:
        confidence = 90.0 + (peak_z_score - 12.0) * (8.0 / 13.0)
    elif peak_z_score >= 7.0:
        confidence = 70.0 + (peak_z_score - 7.0) * (20.0 / 5.0)
    elif peak_z_score >= 4.0:
        confidence = 40.0 + (peak_z_score - 4.0) * (30.0 / 3.0)
    else:
        confidence = max(0.0, peak_z_score * 10.0)

    return offset_sec, {
        "peak_z_score": peak_z_score,
        "confidence": confidence,
        "lag_frames": lag_frames,
        "offset_sec": offset_sec
    }


def compute_cross_correlation(s_ref, s_target, sr):
    """
    Compute time offset and statistical significance confidence score using 1D FFT cross-correlation on raw waveforms.
    Retained for backward compatibility and Tier 3 fallback ladder.
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


def sync_single_target(ref_info, target_video, tmpdir, sr=8000, max_dur=None, full_scan=False, refine_subframe=True):
    """
    Synchronization task worker for a single target camera.
    Implements 3-tier fallback ladder:
    1. Fast MFCC 120s scan (if not full_scan and target/sample duration allows)
    2. Full-length MFCC scan
    3. Raw waveform 1D FFT cross-correlation fallback
    Includes sub-frame refinement down to single audio sample (< 0.125ms error).
    """
    t0 = time.time()
    target_basename = os.path.basename(target_video)
    target_wav = os.path.join(tmpdir, f"target_{target_basename}.wav")
    fast_wav = os.path.join(tmpdir, f"fast_{target_basename}.wav")

    # Item 5: Probe true media duration using ffprobe
    target_true_dur = probe_media_duration(target_video)

    fbank = ref_info.get("fbank")
    dct_mat = ref_info.get("dct_mat")
    m_ref = ref_info.get("m_ref")
    s_ref = ref_info.get("s_ref")
    f_ref_cache = ref_info.get("f_ref_cache")
    cache_lock = ref_info.get("cache_lock")
    hop_length = ref_info.get("hop_length", 128)

    t_extract = 0.0
    t_calc_start = 0.0
    s_target = None
    target_wav_dur = 0.0

    offset_sec = 0.0
    stats = {"confidence": 0.0, "peak_z_score": 0.0}
    tier_used = "full_mfcc"

    # Fast 120s scan qualification:
    # Only if not full_scan, and max_dur is either None or > 120, and true_dur (if known) > 120
    can_fast_scan = (
        not full_scan
        and (max_dur is None or max_dur > 120.0)
        and (target_true_dur is None or target_true_dur > 120.0)
    )

    if can_fast_scan:
        try:
            extract_audio_track(target_video, fast_wav, sr=sr, max_duration=120.0)
            s_tgt_fast, _, _ = load_and_preprocess_audio(fast_wav)
            m_tgt_fast = compute_mfcc(s_tgt_fast, sr=sr, hop_length=hop_length, fbank=fbank, dct_mat=dct_mat)

            # Sliced ref MFCC for the initial 120s
            frames_120 = int(round(120.0 * sr / hop_length))
            m_ref_fast = m_ref[:, :min(m_ref.shape[1], frames_120)]

            fast_offset, fast_stats = compute_mfcc_cross_correlation(
                m_ref_fast, m_tgt_fast, hop_length=hop_length, sr=sr
            )

            # Check BBC High confidence threshold (>= SCORE_HIGH) and plausible offset within 120s
            if fast_stats["peak_z_score"] >= SCORE_HIGH and abs(fast_offset) < 110.0:
                offset_sec = fast_offset
                stats = fast_stats
                tier_used = "fast_120s"
                s_target = s_tgt_fast
                target_wav_dur = len(s_tgt_fast) / sr
                t_extract = time.time()
                t_calc_start = t_extract
                if refine_subframe:
                    offset_sec = refine_offset_subframe(s_ref, s_tgt_fast, offset_sec, sr=sr)
        except Exception:
            pass

    if tier_used != "fast_120s":
        # Tier 2: Full-length MFCC scan
        extract_audio_track(target_video, target_wav, sr=sr, max_duration=max_dur)
        t_extract = time.time()
        t_calc_start = t_extract

        s_target, _, target_wav_dur = load_and_preprocess_audio(target_wav)
        m_target = compute_mfcc(s_target, sr=sr, hop_length=hop_length, fbank=fbank, dct_mat=dct_mat)

        offset_sec, stats = compute_mfcc_cross_correlation(
            m_ref, m_target, hop_length=hop_length, sr=sr,
            f_ref_cache=f_ref_cache, lock=cache_lock
        )

        # Check if Tier 2 meets medium confidence (>= SCORE_LOW)
        if stats["peak_z_score"] >= SCORE_LOW:
            tier_used = "full_mfcc"
            if refine_subframe:
                offset_sec = refine_offset_subframe(s_ref, s_target, offset_sec, sr=sr)
        else:
            # Tier 3: Raw waveform fallback ladder
            tier_used = "raw_waveform_fallback"
            raw_offset, raw_stats = compute_cross_correlation(s_ref, s_target, sr=sr)
            if raw_stats["peak_z_score"] > stats["peak_z_score"]:
                offset_sec = raw_offset
                stats = raw_stats
            elif refine_subframe:
                offset_sec = refine_offset_subframe(s_ref, s_target, offset_sec, sr=sr)

    t_calc = time.time()
    final_duration = target_true_dur if target_true_dur is not None else target_wav_dur

    return {
        "target_video": target_video,
        "target_basename": target_basename,
        "duration_sec": final_duration,
        "offset_sec": offset_sec,
        "confidence": stats["confidence"],
        "peak_z_score": stats["peak_z_score"],
        "extract_time": (t_extract - t0) if t_extract > 0 else 0.0,
        "calc_time": (t_calc - t_calc_start) if t_calc_start > 0 else 0.0,
        "total_time": t_calc - t0,
    }


def sync_all_targets(ref_video, target_videos, sr=8000, sample_dur=None, workers=4, full_scan=False, refine_subframe=True):
    """
    Main orchestration function for multi-camera global time alignment.
    Executes MFCC-based cross correlation with sub-frame refinement.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Reference camera
        ref_true_dur = probe_media_duration(ref_video)
        ref_wav = os.path.join(tmpdir, "ref_anchor.wav")
        extract_audio_track(ref_video, ref_wav, sr=sr, max_duration=sample_dur)
        s_ref, _, ref_wav_dur = load_and_preprocess_audio(ref_wav)
        final_ref_dur = ref_true_dur if ref_true_dur is not None else ref_wav_dur

        hop_length = 128
        fbank = mel_filterbank(sr, n_fft=256, n_mels=26)
        dct_mat = dct2_matrix(n_out=13, n_in=26)
        m_ref = compute_mfcc(s_ref, sr=sr, hop_length=hop_length, fbank=fbank, dct_mat=dct_mat)

        ref_info = {
            "s_ref": s_ref,
            "m_ref": m_ref,
            "f_ref_cache": {},
            "cache_lock": threading.Lock(),
            "fbank": fbank,
            "dct_mat": dct_mat,
            "hop_length": hop_length,
            "duration_sec": final_ref_dur,
            "path": ref_video,
            "basename": os.path.basename(ref_video),
            "sr": sr
        }
        print(f"  ✓ Reference audio extracted & MFCC indexed ({ref_info['basename']})")

        # 2. Parallel processing for all target cameras
        results = []
        scan_mode_str = "Full-Scan" if full_scan else "Fast-Ladder (120s -> Full)"
        refine_str = "Subframe Refinement (0.125ms)" if refine_subframe else "Hop-Level (16ms)"
        print(f"  ► Calculating MFCC cross-correlation for {len(target_videos)} target camera(s) [{scan_mode_str} | {refine_str}]...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(sync_single_target, ref_info, tgt, tmpdir, sr, sample_dur, full_scan, refine_subframe): tgt
                for tgt in target_videos
            }
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                results.append(res)
                score = res["peak_z_score"]
                if score >= SCORE_HIGH:
                    marker = "✓"
                    verb = "Aligned"
                elif score >= SCORE_LOW:
                    marker = "ℹ"
                    verb = "Aligned (marginal)"
                else:
                    marker = "⚠️"
                    verb = "LOW CONFIDENCE"

                print(f"    {marker} {verb} {res['target_basename']} (Δt: {res['offset_sec']:+.3f}s | Conf: {res['confidence']:.1f}% | Score: {res['peak_z_score']:.1f}) in {res['total_time']:.2f}s")

                if score < SCORE_LOW:
                    sys.stderr.write(
                        f"[Warning] {res['target_basename']}: audio alignment confidence is low (score {res['peak_z_score']:.1f}, {res['confidence']:.1f}%).\n"
                        f"The computed offset of {res['offset_sec']:+.3f}s may be wrong. Common causes: the cameras\n"
                        f"share no audible content, one recording is silent over the analysed range,\n"
                        f"or --sample-dur covers only a silent section.\n"
                        f"Consider re-running with --full-scan, or set the range manually with\n"
                        f"--ref-start / --ref-end.\n"
                    )
                    sys.stderr.flush()

    results.sort(key=lambda r: target_videos.index(r["target_video"]))
    return ref_info, results


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

