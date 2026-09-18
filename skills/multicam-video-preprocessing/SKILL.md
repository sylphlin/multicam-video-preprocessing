---
name: multicam-video-preprocessing
description: >
  Universal multi-camera video preprocessing and AI editing suite for 2 to 6 camera setups.
  Executes MFCC acoustic time alignment with subframe refinement (<0.125ms), EBU R128 two-pass linear loudness normalization (-14 LUFS),
  synchronized full-length camera master exporting (frame-accurate hardware re-encoding), Multi-in-One compact grid composition (canvas <= 1920x1080, min >= 640x480/CAM),
  zero-split Gemini 3.8 Flash Agentic Video EDL generation (100% Google Cloud Vertex AI via ADC + GCS storage with 2-day lifecycle auto-cleanup), FCP7 XML timeline export with NTSC fractional fps & drop-frame support (Primary), direct video rendering (Secondary),
  and 3-stage YouTube subtitles generation (Whisper + Vertex AI Gemini 1M Proofreading via GCS).
  Keywords: multicam, multi-camera, dual-cam, 4-cam, 6-cam, time alignment, audio sync, loudness normalization, video preprocessing, multicam pipeline, multi-in-one, token optimization, fcp7 xml, subtitles, agentic video, vertex ai, gcs, adc.
---

# Multi-Camera Video Pipeline & AI Editing Suite (Antigravity Native Skill)

Universal end-to-end toolkit for multi-camera video production (2 to 6 Cameras), AI-assisted long-form video editing (Gemini 3.8 Flash 1M Token Context via Vertex AI), and professional NLE timeline export (DaVinci Resolve / Adobe Premiere Pro / Final Cut Pro).

---

## 🛠️ Prerequisites & Environment

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg** (with `h264_videotoolbox` hardware encoding and `loudnorm` filter support)
- **Python 3.8+** with `numpy`, `google-genai`, `google-cloud-storage`
- **Cloud Credentials (100% ADC + Vertex AI & GCS)**:
  - Authenticate via `gcloud auth application-default login`
  - Run `./setup.sh` once to automatically provision the GCS bucket (`gs://multicam-video-${GOOGLE_CLOUD_PROJECT}`), tiered Lifecycle auto-cleanup rules (`raw/` staging: 2 days; `output/`, `deliverables/`, `multicam_assets/` deliverables: 15 days), Vertex AI Service Agent IAM (`roles/storage.objectUser`), and `.env` configuration.

---

## 📁 Modular Toolset Architecture

| Step | Script | Core Module (`scripts/modules/`) | Function |
| :--- | :--- | :--- | :--- |
| **Step 1** | `scripts/multicam_pipeline.py` | `audio_sync.py`, `audio_normalizer.py`, `video_composer.py` | MFCC Acoustic Sync + Subframe Refinement (0.125ms), EBU R128 (-14 LUFS), Synced Masters, Multi-in-One Full Grid (`multicam_merged_full.mp4`) |
| **Step 2** | `scripts/generate_edl.py` | `llm_client.py`, `gcp_client.py`, `progress.py`, `edl_validator.py`, `assets/edl_interview_template.md` | Vertex AI Gemini 3.8 Flash Agentic Video Understanding (ADC + GCS `gs://` URI) + EDL Validation -> `edl_full.csv` + Report |
| **Step 3A** | `scripts/export_fcp7_xml.py` | `reporter.py`, `time_utils.py`, `edl_validator.py` | Full-length EDL CSV -> FCP7 XML (`final_cut_full.xml`) for DaVinci / Premiere (EDL validation, NTSC float fps & drop-frame support) |
| **Step 3B** | `scripts/edl_to_video.py` | `video_composer.py`, `edl_validator.py` | Hardware-accelerated clip cutting directly from synced masters -> `final_cut_full.mp4` (with EDL validation) |
| **Step 4** | `scripts/generate_subtitles.py` | `llm_client.py`, `gcp_client.py`, `progress.py`, `assets/subtitle_proofread_template.*.md` | Whisper Word Timestamps + Chunk-Scoped Acoustic Reprojection + Vertex AI Gemini 1M Proofreading (ADC + GCS) -> `.srt` / `.vtt` |

---

## 🔬 Core Technical Principles

1. **MFCC Acoustic Time Alignment & Subframe Refinement (<0.125ms Accuracy)**:
   - Multi-camera synchronization is computed via pure numpy MFCC cross-correlation with a 3-tier fallback ladder (Fast 120s scan -> Full-length MFCC -> Raw waveform fallback) and localized time-domain subframe acoustic refinement, achieving sub-millisecond physical accuracy (<0.125ms, single audio sample at 8kHz) with 97.7% lower memory consumption. Evaluated against BBC standard score thresholds ($Z \ge 12.0$ High `✓ Aligned`, $7.0 \le Z < 12.0$ Medium `ℹ Aligned (marginal)`, $Z < 7.0$ Low `⚠️ LOW CONFIDENCE` + stderr warnings & Summary Gate). Passing `--strict-sync` exits non-zero on low confidence. Synchronized camera masters (`CAM*_synced.mp4`) default to hardware-accelerated frame-accurate re-encoding (`h264_videotoolbox` / `libx264 -crf 18`), eliminating stream-copy keyframe snapping drift and black-frame stutter.
2. **EBU R128 Two-Pass Linear Loudness Normalization**:
   - Audio tracks are normalized to $-14.0\text{ LUFS}$ ($LRA=11.0\text{ LU}$, $TP=-1.5\text{ dBTP}$) compliant with YouTube broadcast standards. Uses two-pass analysis: Pass 1 null-sink acoustic measurement, Pass 2 linear gain offset (`linear=true`) to eliminate dynamic pumping artifacts.
3. **Zero-Split Agentic Video Architecture (No Chapter Slicing Required)**:
   - Evaluates full-length multicam footage (>1 hour) end-to-end via Vertex AI Gemini 3.8 Flash Agentic Video Understanding (`processing="agentic"`). Goal-directed sparse sampling reduces token usage by **99.7%** (from ~1,000,000 to ~3,000 tokens), completely eliminating sentence bisection and multi-part complexity.
4. **Token-Optimized Compact Grid Composition**:
   - Merges 2 to 6 camera angles into a single multi-view canvas ($\le 1920 \times 1080$, each CAM $\ge 640 \times 480$), reducing AI multimodal token consumption by **50% to 83%**.
5. **Universal Pre-roll & Countdown Elimination (Zero-Tolerance & Asymmetric Safety Margin)**:
   - Systematically purges all on-set countdown noises ("5, 4, 3, 2, 1", "五四三二", "Ready Action") and pre-roll clutter. Enforces asymmetric safety margins where the start point is self-verified on the `[Start, Start+2s]` window to guarantee the opening frame aligns cleanly with the speaker's true opening word.
6. **Three-Stage Golden Standard Subtitles (Whisper Word Timestamps + Vertex AI Gemini Multimodal + 429 Retry)**:
   - Stage 1 extracts 1M context global domain glossary from GCS-staged audio. Stage 2 extracts Whisper physical word timestamps cached to `_words.json` for instant re-runs. Stage 3 performs chunk-scoped acoustic reprojection via Vertex AI + GCS with automatic exponential backoff & jitter retry handling HTTP 429 and transient rate limits, followed by rhythm sanitization (anti-flicker gap bridging $< 0.6\text{s}$, breathing buffer $+0.4\text{s}$, monotonic forward continuity, 0 micro-flickers/overlaps).
7. **100% Pure Vertex AI (ADC) & GCS Cloud Architecture (Zero AI Studio Keys)**:
   - All model calls and multimodal media ingestion run exclusively on **Google Cloud Vertex AI** (`GOOGLE_CLOUD_LOCATION=global`) authenticated via Application Default Credentials (`gcloud auth application-default login`). Media assets are staged to **Google Cloud Storage** (`gs://multicam-video-${PROJECT_ID}/raw/`) with SHA-256 hash caching (avoiding redundant large video uploads on same-day re-runs), a 2-day GCS Bucket Lifecycle auto-deletion policy, and optional `--cleanup-gcs` immediate cleanup.
8. **Deterministic EDL Semantic Validation & `--strict-edl` Safeguard**:
   - Pre-flight semantic validation covering 8 structural dimensions across 3 execution points (before disk write in `generate_edl.py`, and on EDL loading in `export_fcp7_xml.py` and `edl_to_video.py`). Evaluates 6 critical ERRORs (`E_NO_ROWS`, `E_PARSE_TIME`, `E_NEGATIVE_DURATION`, `E_NON_MONOTONIC`, `E_OVERLAP`, `E_EMPTY_CAMERA`) and 2 WARNs (`W_UNKNOWN_CAMERA`, `W_GAP`). Passing `--strict-edl` halts execution with exit code 1 on ERROR (default warns and continues; `generate_edl.py` always writes bad data to disk before exit for post-mortem inspection). Supports report localization via `--lang` (built-in `en` and `zh-TW`, with silent fallback to `en`), customizable gap threshold via `--edl-max-gap-sec` (default: `0.05`), and camera whitelist via `--edl-known-cameras` (defaults to `^CAM\d+$` regex in generator, auto-inferred from media directory in exporter/renderer).

---

## 🚀 Execution Instructions

For step-by-step execution, CLI commands, and stage exit gate assertions, refer to the official workflow runbook:
👉 **[multicam_workflow.md](file:///.agent/workflows/multicam_workflow.md)**
