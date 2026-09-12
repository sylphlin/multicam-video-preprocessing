"""
Multi-in-One video composition and grid merging module (Video Composer Module).
Merges 2 to 6+ synchronized camera streams into a single multi-view grid/side-by-side video.
Constraints:
  - Max total canvas: 1920x1080 (Compact Canvas Option B)
  - Native 16:9 cell aspect ratio preservation across all camera setups (540P for 2-4 CAMs, 360P for 5-6 CAMs)
  - Pixel format normalization (yuv420p) & setsar=1
  - Automatic CAM1..CAMn text labeling with robust fallback
  - Purpose: Drastically reduces token consumption for multimodal LLMs (e.g. Gemini 3.8 Flash Agentic Video).
"""

import math
import os
import subprocess
import time

try:
    from .progress import LiveTicker
    from .time_utils import format_seconds
except ImportError:
    from modules.progress import LiveTicker
    from modules.time_utils import format_seconds


def compute_grid_spec(num_inputs):
    """
    Calculate optimal grid columns, rows, cell dimensions, and total canvas for 2 to 6+ cameras.
    - 2 CAMs: 1x2 (960x540 per cell -> 1920x540 total canvas, 16:9 native)
    - 3-4 CAMs: 2x2 (960x540 per cell -> 1920x1080 total canvas, 16:9 native)
    - 5-6 CAMs: 3x2 (640x360 per cell -> 1920x720 total canvas, 16:9 native)
    - >6 CAMs: 3xN (640x360 per cell -> 1920x(N*360) canvas)
    """
    if num_inputs <= 2:
        cols, rows = 2, 1
        cw, ch = 960, 540
    elif num_inputs <= 4:
        cols, rows = 2, 2
        cw, ch = 960, 540
    elif num_inputs <= 6:
        cols, rows = 3, 2
        cw, ch = 640, 360
    else:  # >6 CAMs dynamic fallback
        cols = 3
        rows = math.ceil(num_inputs / 3)
        cw, ch = 640, 360

    total_w = cols * cw
    total_h = rows * ch
    return {
        "cols": cols,
        "rows": rows,
        "cell_width": cw,
        "cell_height": ch,
        "total_width": total_w,
        "total_height": total_h
    }


def generate_grid_filter_complex(num_inputs, custom_cw=None, custom_ch=None, draw_labels=True):
    """
    Generate optimal FFmpeg filter_complex string for 2 to 6+ input video streams.
    Applies aspect-ratio preserving scaling, setsar=1, pixel format normalization (yuv420p),
    optional camera position labels ('CAM1', 'CAM2'...), and xstack layout with black fill.
    """
    if num_inputs == 1:
        return "[0:v]null[out]"

    spec = compute_grid_spec(num_inputs)
    cw = custom_cw or spec["cell_width"]
    ch = custom_ch or spec["cell_height"]
    cols = spec["cols"]

    scale_parts = []
    stack_inputs = []
    layout_parts = []

    for i in range(num_inputs):
        # Aspect-ratio preserving scale with center pad and square pixels
        vf_chain = [
            "format=yuv420p",
            f"scale={cw}:{ch}:force_original_aspect_ratio=decrease",
            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1"
        ]
        if draw_labels:
            # Burn in clean semi-transparent camera label in top-left corner
            vf_chain.append(
                f"drawtext=text='CAM{i+1}':x=16:y=16:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4"
            )

        filter_chain_str = ",".join(vf_chain)
        scale_parts.append(f"[{i}:v]{filter_chain_str}[v{i}]")
        stack_inputs.append(f"[v{i}]")

        c = i % cols
        r = i // cols
        x_expr = f"{c * cw}" if c > 0 else "0"
        y_expr = f"{r * ch}" if r > 0 else "0"
        layout_parts.append(f"{x_expr}_{y_expr}")

    layout_str = "|".join(layout_parts)
    stack_str = "".join(stack_inputs) + f"xstack=inputs={num_inputs}:layout={layout_str}:fill=black[out]"
    return f"{';'.join(scale_parts)};{stack_str}"


def compose_multicam_video(video_paths, output_path,
                           video_bitrate="2000k", audio_bitrate="192k",
                           encoder="h264_videotoolbox",
                           draw_labels=True):
    """
    Compose 2 to 6+ synchronized camera videos into a single multi-in-one grid video directly in Python.
    Features automatic fallback for drawtext and hardware encoding.
    """
    if len(video_paths) == 0:
        return 0.0

    t0 = time.time()
    num_inputs = len(video_paths)

    def _build_cmd(use_drawtext, enc):
        fc = generate_grid_filter_complex(num_inputs, draw_labels=use_drawtext)
        c = ["ffmpeg", "-y"]
        for vp in video_paths:
            c.extend(["-i", vp])
        c.extend([
            "-filter_complex", fc,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", enc,
            "-b:v", video_bitrate,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-shortest",
            output_path
        ])
        return c

    cmd = _build_cmd(draw_labels, encoder)

    with LiveTicker(f"Composing multi-in-one grid ({num_inputs} cameras → {os.path.basename(output_path)})"):
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            # Fallback 1: retry without drawtext if drawtext caused the failure
            if draw_labels:
                cmd = _build_cmd(False, encoder)
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

            # Fallback 2: retry with libx264 software encoder if hardware encoder failed
            if res.returncode != 0 and encoder != "libx264":
                cmd = _build_cmd(draw_labels, "libx264")
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0 and draw_labels:
                    cmd = _build_cmd(False, "libx264")
                    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

            if res.returncode != 0:
                err_msg = res.stderr[-500:] if res.stderr else "Unknown error"
                raise RuntimeError(f"FFmpeg multi-in-one composition failed ({num_inputs} cameras): {err_msg}")

    return time.time() - t0


def cut_single_clip(video_path, output_path, start_sec, end_sec,
                    norm_audio_path=None, copy_codec=False,
                    video_bitrate="6000k", audio_bitrate="192k",
                    encoder="h264_videotoolbox"):
    """
    Cut video sub-clip with frame-accurate synchronization:
    - If copy_codec=True: stream-copy (-c copy) for fast keyframe-snapped cutting.
    - If copy_codec=False (default): frame-accurate re-encoding (h264_videotoolbox / libx264)
      ensuring 0.000s sub-frame alignment without keyframe skipping or freeze frames.
    - If norm_audio_path is provided: muxes synchronized video with EBU R128 normalized audio.
    """
    if start_sec < 0:
        start_sec = 0.0
    dur_sec = max(0.0, end_sec - start_sec)

    def _build_cmd(use_copy, enc):
        c = ["ffmpeg", "-y"]
        if norm_audio_path and os.path.exists(norm_audio_path):
            c.extend([
                "-ss", format_seconds(start_sec),
                "-i", video_path,
                "-ss", format_seconds(start_sec),
                "-i", norm_audio_path,
                "-t", format_seconds(dur_sec),
                "-map", "0:v:0",
                "-map", "1:a:0"
            ])
            if use_copy:
                c.extend(["-c", "copy"])
            else:
                if enc == "h264_videotoolbox":
                    c.extend(["-c:v", enc, "-b:v", video_bitrate, "-pix_fmt", "yuv420p"])
                else:
                    c.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"])
                c.extend(["-c:a", "copy"])
        else:
            c.extend([
                "-ss", format_seconds(start_sec),
                "-i", video_path,
                "-t", format_seconds(dur_sec)
            ])
            if use_copy:
                c.extend(["-c", "copy"])
            else:
                if enc == "h264_videotoolbox":
                    c.extend(["-c:v", enc, "-b:v", video_bitrate, "-pix_fmt", "yuv420p"])
                else:
                    c.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"])
                c.extend(["-c:a", "aac", "-b:a", audio_bitrate])
        c.append(output_path)
        return c

    cmd = _build_cmd(copy_codec, encoder)
    t0 = time.time()
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if res.returncode != 0:
        # Fallback to libx264 if hardware encoder failed
        if not copy_codec and encoder != "libx264":
            cmd = _build_cmd(False, "libx264")
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        if res.returncode != 0:
            err_msg = res.stderr[-600:] if res.stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg video cutting failed ({os.path.basename(video_path)}): {err_msg}")

    return time.time() - t0

