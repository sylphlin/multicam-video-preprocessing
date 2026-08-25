"""
Multi-in-One video composition and grid merging module (Video Composer Module).
Merges 2 to 6 synchronized camera streams into a single multi-view grid/side-by-side video.
Constraints:
  - Max total canvas: 1920x1080 (Compact Canvas Option B)
  - Min per-camera resolution: 640x480 (540P for 2-4 CAMs, 480P for 5-6 CAMs)
  - Purpose: Drastically reduces token consumption for multimodal LLMs (e.g. Gemini 1.5/2.0).
"""

import math
import os
import subprocess
import time


def compute_grid_spec(num_inputs):
    """
    Calculate optimal grid columns, rows, cell dimensions, and total canvas for 2 to 6 cameras.
    - 2 CAMs: 1x2 (960x540 per cell -> 1920x540 total)
    - 3-4 CAMs: 2x2 (960x540 per cell -> 1920x1080 total)
    - 5-6 CAMs: 2x3 (640x480 per cell -> 1920x960 total)
    """
    if num_inputs <= 2:
        cols, rows = 2, 1
        cw, ch = 960, 540
    elif num_inputs <= 4:
        cols, rows = 2, 2
        cw, ch = 960, 540
    else:  # 5 or 6 CAMs
        cols, rows = 3, 2
        cw, ch = 640, 480

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


def generate_grid_filter_complex(num_inputs, custom_cw=None, custom_ch=None):
    """
    Generate optimal FFmpeg filter_complex string for 2 to 6 input video streams.
    """
    if num_inputs == 1:
        return "[0:v]null[out]"

    spec = compute_grid_spec(num_inputs)
    cw = custom_cw or spec["cell_width"]
    ch = custom_ch or spec["cell_height"]
    cols = spec["cols"]

    if num_inputs == 2 and not custom_cw:
        filter_str = (
            f"[0:v]scale={cw}:{ch}[v0];"
            f"[1:v]scale={cw}:{ch}[v1];"
            f"[v0][v1]hstack=inputs=2[out]"
        )
        return filter_str

    scale_parts = []
    stack_inputs = []
    layout_parts = []

    for i in range(num_inputs):
        scale_parts.append(f"[{i}:v]scale={cw}:{ch}[v{i}]")
        stack_inputs.append(f"[v{i}]")
        c = i % cols
        r = i // cols
        x_expr = f"{c * cw}" if c > 0 else "0"
        y_expr = f"{r * ch}" if r > 0 else "0"
        layout_parts.append(f"{x_expr}_{y_expr}")

    layout_str = "|".join(layout_parts)
    stack_str = "".join(stack_inputs) + f"xstack=inputs={num_inputs}:layout={layout_str}[out]"
def compose_multicam_video(video_paths, output_path,
                           video_bitrate="4000k", audio_bitrate="192k",
                           encoder="h264_videotoolbox"):
    """
    Compose 2 to 6 synchronized camera videos into a single multi-in-one grid video directly in Python.
    """
    if len(video_paths) == 0:
        return 0.0

    t0 = time.time()
    num_inputs = len(video_paths)
    filter_complex = generate_grid_filter_complex(num_inputs)

    cmd = ["ffmpeg", "-y"]
    for vp in video_paths:
        cmd.extend(["-i", vp])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a",
        "-c:v", encoder,
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-shortest",
        output_path
    ])

    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        if encoder != "libx264":
            cmd[cmd.index(encoder)] = "libx264"
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        if res.returncode != 0:
            err_msg = res.stderr[-500:]
            raise RuntimeError(f"FFmpeg multi-in-one composition failed ({num_inputs} cameras): {err_msg}")

    return time.time() - t0
