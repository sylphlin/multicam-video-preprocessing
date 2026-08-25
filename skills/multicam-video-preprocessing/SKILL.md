---
name: multicam-video-preprocessing
description: >
  Universal multi-camera video pipeline and AI editing suite for 2 to 6 camera setups.
  Executes global FFT time alignment, EBU R128 audio loudness normalization,
  30-40 min natural pause chapter segmentation for 1M context AI video editing,
  synchronized full-length camera master exporting, Multi-in-One grid composition (compact canvas <= 1920x1080, min >= 640x480/CAM),
  Gemini multimodal EDL generation, FCP7 XML timeline export (Primary), and direct video rendering (Secondary).
  Keywords: multicam, multi-camera, dual-cam, 4-cam, 6-cam, time alignment, audio sync, loudness normalization, chapter splitting, auto split, video preprocessing, multicam pipeline, multi-in-one, token optimization, fcp7 xml.
---

# Multi-Camera Video Pipeline & AI Editing Suite

Universal end-to-end toolkit for multi-camera video production (2 to 6 Cameras), AI-assisted long-form video editing pipelines (1M Token Context), and professional NLE timeline export (DaVinci Resolve / Premiere Pro / Final Cut Pro).

## 🛠️ Prerequisites

- `ffmpeg` (with `h264_videotoolbox` and `loudnorm` filter support)
- Python 3.8+ with `numpy`

## 📁 Toolset Scripts & Skills

| Step | Script | Dedicated Skill | Function |
| :--- | :--- | :--- | :--- |
| **1. Pipeline Engine** | `scripts/multicam_pipeline.py` | `multicam-video-preprocessing` | Time sync, EBU R128, 30-40m splitting, Synced Masters, Multi-in-One grid |
| **2. AI EDL** | `scripts/generate_edl.py` | `gemini-edl-generation` | Prompt asset + Gemini 3.7 Flash -> `edl_partX.csv` + Report |
| **3A. NLE XML (⭐ Primary)** | `scripts/export_fcp7_xml.py` | `fcp7-xml-export` | Multi-part EDL CSV -> FCP7 XML (`final_cut_full.xml`) for NLEs |
| **3B. Direct Render (🎬 Preview)** | `scripts/edl_to_video.py` | `edl-video-rendering` | EDL CSV -> Hardware-accelerated `final_cut_partX.mp4` |
| **3B. Concat (🎬 Preview)** | `scripts/concat_videos.py` | `video-concatenation` | Lossless Concat -> Full episode `final_cut_full.mp4` |

---

## 🧭 Workflow Branching Decision Guide (Agent Instructions)

When processing multi-camera projects, follow this decision tree:

1. **Step 1 & Step 2 (Always Required)**:
   - Run `scripts/multicam_pipeline.py` on raw footage.
   - Run Gemini AI EDL generation (or `generate_edl.py`) on `multicam_merged_part*.mp4`.

2. **Branch Decision (Choose Path A or Path B)**:
   - 🌟 **Path A: Professional NLE Editing Timeline (Default / 90% Use Case)**
     - *Trigger*: User mentions DaVinci Resolve, Premiere Pro, Final Cut Pro, XML, NLE, timeline editing, color grading, or audio mastering.
     - *Action*: Execute **Step 3A (`export_fcp7_xml.py`)** to output `final_cut_full.xml`.
   - 🎬 **Path B: Direct MP4 Video Preview (Secondary / 10% Use Case)**
     - *Trigger*: User explicitly requests a rendered video file, quick preview MP4, or headless server video assembly without opening an NLE.
     - *Action*: Execute **Step 3B (`edl_to_video.py` & `concat_videos.py`)** to output `final_cut_full.mp4`.

---

## 🚀 Recipes & Command Examples

### Recipe 1: Professional NLE Timeline Workflow (Recommended ⭐)

```bash
# 1. Multicam Pipeline (Sync + EBU R128 + Auto Split + Grid Merge + Synced Masters)
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --split-min-dur 30 --split-max-dur 40 \
  --normalize --merge \
  -o ./output/

# 2. AI EDL Generation (Gemini 3.7 Flash)
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3A. FCP7 XML Timeline Export (DaVinci Resolve / Premiere Pro)
python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml
```

### Recipe 2: Direct Video Rendering Workflow (Quick Preview 🎬)

```bash
# 1. Multicam Pipeline (Same as Recipe 1)
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --normalize --merge \
  -o ./output/

# 2. AI EDL Generation (Same as Recipe 1)
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3B. Render Each Chapter Part
python3 scripts/edl_to_video.py --edl ./output/edl_part1.csv
python3 scripts/edl_to_video.py --edl ./output/edl_part2.csv

# 3B. Lossless Concatenation to Full Episode Video (concat_videos.py)
python3 scripts/concat_videos.py -d ./output/ -o ./output/final_cut_full.mp4
```
