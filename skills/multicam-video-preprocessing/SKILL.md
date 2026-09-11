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
| **Step 1** | `scripts/multicam_pipeline.py` | `audio_sync.py`, `audio_normalizer.py`, `video_composer.py` | 8kHz FFT Time Sync, EBU R128 (-14 LUFS), Synced Masters, Multi-in-One Full Grid (`multicam_merged_full.mp4`) |
| **Step 2** | `scripts/generate_edl.py` | `llm_client.py`, `progress.py`, `assets/edl_interview_template.md` | Gemini 3.7 Flash Agentic Video Understanding (Zero-Split Pipeline, >1hr video, 99.7% token reduction) -> `edl_full.csv` + Report |
| **Step 3A** | `scripts/export_fcp7_xml.py` | `reporter.py`, `time_utils.py` | Full-length EDL CSV -> FCP7 XML (`final_cut_full.xml`) for DaVinci / Premiere |
| **Step 3B** | `scripts/edl_to_video.py` | `video_composer.py` | Hardware-accelerated clip cutting directly from synced masters -> `final_cut_full.mp4` |
| **Step 4** | `scripts/generate_subtitles.py` | `llm_client.py`, `progress.py`, `assets/subtitle_proofread_template.*.md` | Whisper Word Timestamps + Chunk-Scoped Acoustic Reprojection + Gemini 1M Proofreading -> `.srt` / `.vtt` |

---

## 🔬 Core Technical Principles

1. **8kHz FFT Physical Time Alignment & Frame-Accurate Master Slicing**:
   - Multi-camera synchronization is 100% computed via 8kHz 1D FFT cross-correlation of acoustic waveforms. Time offsets ($\Delta t$) achieve millisecond physical accuracy without speech-to-text reliance. Synchronized camera masters (`CAM*_synced.mp4`) default to hardware-accelerated frame-accurate re-encoding (`h264_videotoolbox` / `libx264 -crf 18`), eliminating stream-copy keyframe snapping drift and black-frame stutter.
2. **EBU R128 Two-Pass Linear Loudness Normalization**:
   - Audio tracks are normalized to $-14.0\text{ LUFS}$ ($LRA=11.0\text{ LU}$, $TP=-1.5\text{ dBTP}$) compliant with YouTube broadcast standards. Uses two-pass analysis: Pass 1 null-sink acoustic measurement, Pass 2 linear gain offset (`linear=true`) to eliminate dynamic pumping artifacts.
3. **Zero-Split Agentic Video Architecture (No Chapter Slicing Required)**:
   - Evaluates full-length multicam footage (>1 hour) end-to-end via Gemini 3.7 Flash Agentic Video Understanding (`processing="agentic"`). Goal-directed sparse sampling reduces token usage by **99.7%** (from ~1,000,000 to ~3,000 tokens), completely eliminating sentence bisection and multi-part complexity.
4. **Token-Optimized Compact Grid Composition**:
   - Merges 2 to 6 camera angles into a single multi-view canvas ($\le 1920 \times 1080$, each CAM $\ge 640 \times 480$), reducing AI multimodal token consumption by **50% to 83%**.
5. **Universal Pre-roll & Countdown Elimination (Zero-Tolerance & Asymmetric Safety Margin)**:
   - Systematically purges all on-set countdown noises ("5, 4, 3, 2, 1", "五四三二", "Ready Action") and pre-roll clutter. Enforces asymmetric safety margins where the start point is self-verified on the `[Start, Start+2s]` window to guarantee the opening frame aligns cleanly with the speaker's true opening word.
6. **Three-Stage Golden Standard Subtitles (Whisper Word Timestamps + Gemini Multimodal + 429 Retry)**:
   - Stage 1 extracts 1M context global domain glossary. Stage 2 extracts Whisper physical word timestamps cached to `_words.json` for instant re-runs. Stage 3 performs chunk-scoped acoustic reprojection with automatic exponential backoff & jitter retry handling HTTP 429 and transient rate limits, followed by rhythm sanitization (anti-flicker gap bridging $< 0.6\text{s}$, breathing buffer $+0.4\text{s}$, monotonic forward continuity, 0 micro-flickers/overlaps).

---

## 🚀 Execution Instructions

For step-by-step execution, CLI commands, and stage exit gate assertions, refer to the official workflow runbook:
👉 **[multicam_workflow.md](file:///.agent/workflows/multicam_workflow.md)**
