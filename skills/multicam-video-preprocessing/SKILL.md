---
name: multicam-video-preprocessing
description: >
  Universal multi-camera video preprocessing and AI editing suite for 2 to 6 camera setups.
  Executes global FFT acoustic time alignment, EBU R128 audio loudness normalization (-14 LUFS),
  30-40 min natural pause chapter segmentation for 1M context AI video editing,
  synchronized full-length camera master exporting, Multi-in-One grid composition (compact canvas <= 1920x1080, min >= 640x480/CAM),
  Gemini multimodal EDL generation, FCP7 XML timeline export (Primary), direct video rendering (Secondary),
  and YouTube subtitles generation (Whisper + Gemini Proofreading).
  Keywords: multicam, multi-camera, dual-cam, 4-cam, 6-cam, time alignment, audio sync, loudness normalization, chapter splitting, auto split, video preprocessing, multicam pipeline, multi-in-one, token optimization, fcp7 xml, subtitles.
---

# Multi-Camera Video Pipeline & AI Editing Suite (Antigravity Native Skill)

Universal end-to-end toolkit for multi-camera video production (2 to 6 Cameras), AI-assisted long-form video editing (Gemini 3.7 Flash 1M Token Context), and professional NLE timeline export (DaVinci Resolve / Adobe Premiere Pro / Final Cut Pro).

---

## 🛠️ Prerequisites & Environment

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg** (with `h264_videotoolbox` hardware encoding and `loudnorm` filter support)
- **Python 3.8+** with `numpy`

---

## 📁 Modular Toolset Architecture

| Step | Script | Core Module (`scripts/modules/`) | Function |
| :--- | :--- | :--- | :--- |
| **Step 1** | `scripts/multicam_pipeline.py` | `audio_sync.py`, `audio_normalizer.py`, `video_segmenter.py`, `video_composer.py` | 8kHz FFT Time Sync, EBU R128 (-14 LUFS), 30-40m Pause Splitting, Synced Masters, Multi-in-One Grid |
| **Step 2** | `scripts/generate_edl.py` | `llm_client.py`, `progress.py`, `assets/edl_interview_template.md` | Gemini 3.7 Flash 1M Context multimodal video inspection -> `edl_part*.csv` + Report |
| **Step 3A** | `scripts/export_fcp7_xml.py` | `reporter.py`, `time_utils.py` | Multi-part EDL CSV -> FCP7 XML (`final_cut_full.xml`) for DaVinci / Premiere |
| **Step 3B** | `scripts/edl_to_video.py` | `video_composer.py` | Hardware-accelerated clip cutting -> `final_cut_part*.mp4` |
| **Step 3B** | `scripts/concat_videos.py` | N/A | Lossless concat -> Full episode `final_cut_full.mp4` |
| **Step 4** | `scripts/generate_subtitles.py` | `llm_client.py`, `progress.py`, `assets/subtitle_proofread_template.*.md` | Whisper ASR millisecond alignment + Gemini 1M Context proofreading -> `.srt` / `.vtt` |

---

## 🔬 Core Technical Principles

1. **8kHz FFT Physical Time Alignment**:
   - Multi-camera synchronization is 100% computed via 8kHz 1D FFT cross-correlation of acoustic waveforms. Time offsets ($\\Delta t$) achieve millisecond physical accuracy without speech-to-text reliance.
2. **EBU R128 Broadcast Loudness Normalization**:
   - Audio tracks are normalized to $-14.0\\text{ LUFS}$ ($LRA=11.0\\text{ LU}$, $TP=-1.5\\text{ dBTP}$) compliant with YouTube and broadcast standards.
3. **30–40 min Natural Pause Chapter Segmentation**:
   - Audio RMS energy scanning detects natural speech breath pauses to slice long footage into 30–40 min chunks, perfectly fitting 1M token context windows while preserving speaker sentence continuity.
4. **Token-Optimized Compact Grid Composition**:
   - Merges 2 to 6 camera angles into a single multi-view canvas ($\\le 1920 \\times 1080$, each CAM $\\ge 640 \\times 480$), reducing AI multimodal token consumption by **50% to 83%**.
5. **Two-Stage Golden Standard Subtitles (Whisper + Gemini)**:
   - Local Whisper ASR provides millisecond-accurate timestamp alignment, while Gemini performs 1M-context global terminology extraction and parallel typo/homophone correction.

---

## 🚀 Execution Instructions

For step-by-step execution, CLI commands, and stage exit gate assertions, refer to the official workflow runbook:
👉 **[multicam_workflow.md](file:///.agent/workflows/multicam_workflow.md)**
