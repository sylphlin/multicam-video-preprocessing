---
description: "Universal 4-stage gated workflow for 2-6 multi-camera audio sync, chapter splitting, Gemini AI rough-cut, NLE XML export, and subtitles."
---

# Multi-Camera Video Editing & Production Workflow

Follow this step-by-step workflow when processing multi-camera projects:

## 🚦 Workflow Stage Gates

```mermaid
flowchart TD
    S1["Stage 1: Multicam Preprocessing<br/>(scripts/multicam_pipeline.py)"] --> G1{"Gate 1 Verification<br/>• multicam_sync.json exists<br/>• multicam_merged_part*.mp4 exists"}
    G1 -->|"Passed"| S2["Stage 2: Gemini AI Rough-Cut<br/>(scripts/generate_edl.py)"]
    S2 --> G2{"Gate 2 Verification<br/>• All edl_part*.csv exist and >0 bytes"}
    G2 -->|"Passed (Primary 90%)"| S3A["Stage 3A: Export Timeline<br/>(scripts/export_fcp7_xml.py)"]
    G2 -->|"Passed (Secondary 10%)"| S3B["Stage 3B: Direct Rendering<br/>(edl_to_video.py + concat)"]
    S3A --> G3A{"Gate 3A Verification<br/>final_cut_full.xml exists"}
    S3B --> G3B{"Gate 3B Verification<br/>final_cut_full.mp4 exists"}
    G3B --> S4["Stage 4: YouTube Subtitles<br/>(scripts/generate_subtitles.py)"]
    S4 --> G4{"Gate 4 Verification<br/>final_cut_full.srt / .vtt exist"}
```

## Step 1: Multi-Camera Preprocessing
- **Command**:
  ```bash
  python3 scripts/multicam_pipeline.py \
    --ref <CAM1.mp4> --targets <CAM2.mp4...> \
    --auto-split --split-min-dur 30 --split-max-dur 40 \
    --normalize --merge -o <OUTPUT_DIR>
  ```
- **Exit Gate 1**: Verify `<OUTPUT_DIR>/multicam_sync.json` and `<OUTPUT_DIR>/multicam_merged_part*.mp4` exist.

## Step 2: Gemini AI Multimodal Rough-Cut
- **Command** (run for EVERY part produced in Step 1):
  ```bash
  python3 scripts/generate_edl.py -v <OUTPUT_DIR>/multicam_merged_part1.mp4
  python3 scripts/generate_edl.py -v <OUTPUT_DIR>/multicam_merged_part2.mp4  # If Part 2 exists
  ```
- **Exit Gate 2**: Verify all corresponding `<OUTPUT_DIR>/edl_part*.csv` exist and size > 0 bytes.

## Step 3A: NLE Timeline Export (Primary ⭐)
- **Command**:
  ```bash
  python3 scripts/export_fcp7_xml.py -d <OUTPUT_DIR> -o <OUTPUT_DIR>/final_cut_full.xml
  ```
- **Exit Gate 3A**: Verify `<OUTPUT_DIR>/final_cut_full.xml` exists. Provide DaVinci Resolve / Premiere Pro import guide.

## Step 3B: Direct Rendering (Secondary 🎬)
- **Command**:
  ```bash
  python3 scripts/edl_to_video.py --edl <OUTPUT_DIR>/edl_part1.csv
  python3 scripts/concat_videos.py -d <OUTPUT_DIR> -o <OUTPUT_DIR>/final_cut_full.mp4
  ```
- **Exit Gate 3B**: Verify `<OUTPUT_DIR>/final_cut_full.mp4` exists with duration > 0.

## Step 4: YouTube Subtitles (On-Demand 📝)
- **Command**:
  ```bash
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4
  ```
- **Exit Gate 4**: Verify `<OUTPUT_DIR>/final_cut_full.srt` and `.vtt` exist.
