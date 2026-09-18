---
description: "Universal 4-stage gated execution runbook for 2 to 6 camera audio sync, Gemini 3.8 Flash Agentic Video zero-split rough-cut, NLE XML export, and subtitles."
---

# Multi-Camera AI Preprocessing & Video Editing Workflow (Zero-Split Agentic Architecture)

This runbook defines the exact execution sequence, stage gates, CLI commands, and verification criteria for processing multi-camera video footage in Antigravity.

Powered by **Gemini 3.8 Flash Agentic Video Understanding**, footage of any length (>1 hour) is processed end-to-end as a unified full-length timeline without requiring chapter segmentation (Zero-Split Pipeline), eliminating boundary speech bisection and slashing token consumption by 99.7%.

---

## 🚦 4-Stage Gated Workflow Architecture

```mermaid
flowchart TD
    S1["Stage 1: Multicam Preprocessing<br/>(scripts/multicam_pipeline.py --normalize --merge)"] --> G1{"Gate 1 Verification<br/>• multicam_sync.json exists<br/>• multicam_merged_full.mp4 exists<br/>• *_synced.mp4 masters exist"}
    G1 -->|"Passed"| S2["Stage 2: Agentic Video Rough-Cut<br/>(scripts/generate_edl.py)"]
    S2 --> G2{"Gate 2 Verification<br/>• edl_full.csv exists and >0 bytes<br/>• EDL semantic validation (0 ERROR)<br/>• Zero countdown residue"}
    G2 -->|"Passed (Primary 90%)"| S3A["Stage 3A: Export Timeline<br/>(scripts/export_fcp7_xml.py)"]
    G2 -->|"Passed (Secondary 10%)"| S3B["Stage 3B: Direct Rendering<br/>(scripts/edl_to_video.py)"]
    S3A --> G3A{"Gate 3A Verification<br/>final_cut_full.xml exists"}
    S3B --> G3B{"Gate 3B Verification<br/>final_cut_full.mp4 exists"}
    G3B --> S4["Stage 4: YouTube Subtitles<br/>(scripts/generate_subtitles.py)"]
    S4 --> G4{"Gate 4 Verification<br/>final_cut_full.srt / .vtt exist"}
```

---

## 📋 Stage-by-Stage Execution Runbook

### Stage 1: Physical Preprocessing (Sync, Normalization, Master Export, Grid Merge)
- **Goal**: Global MFCC acoustic time alignment with subframe refinement (<0.125ms), BBC standard confidence evaluation, EBU R128 (-14 LUFS) broadcast two-pass linear audio normalization (`linear=true`), full-length frame-accurate synchronized camera masters export (`CAM*_synced.mp4`, hardware re-encoded by default; `--stream-copy` available), and compact multi-in-one grid composition (`multicam_merged_full.mp4`, max $\le 1920 \times 1080$, min $\ge 640 \times 480$/CAM).
- **Execution Command (Standard Zero-Split Flow)**:
  ```bash
  python3 scripts/multicam_pipeline.py \
    --ref <CAM1.mp4> --targets <CAM2.mp4...> \
    --normalize --merge -o <OUTPUT_DIR>
  ```
- **Exit Gate 1 Verification**:
  - [x] `<OUTPUT_DIR>/multicam_sync.json` exists with valid offset data.
  - [x] `<OUTPUT_DIR>/<CAM>_synced.mp4` full-length synchronized masters exist for all cameras.
  - [x] `<OUTPUT_DIR>/multicam_merged_full.mp4` (or `multicam_merged_synced.mp4`) grid video exists.
  - 🚨 *Do NOT proceed to Stage 2 until all Gate 1 criteria pass.*

---

### Stage 2: Gemini AI Multimodal Rough-Cut (Agentic Video EDL Generation)
- **Goal**: Gemini 3.8 Flash Agentic Video Understanding (`processing="agentic"`) dynamically inspects the full-length grid video using `assets/edl_interview_template.md` prompt rules. Eliminates pre/post-roll waste with Zero-Tolerance countdown purging & `[Start, Start+2.0s]` self-verification.
- **EDL Semantic Validation & Safeguards**:
  - Built-in deterministic validator inspects 8 structural dimensions:
    - **6 ERRORs**: `E_NO_ROWS` (no data rows), `E_PARSE_TIME` (unparseable timecode), `E_NEGATIVE_DURATION` (duration <= 0), `E_NON_MONOTONIC` (start time goes backward), `E_OVERLAP` (shot overlaps previous), `E_EMPTY_CAMERA` (camera field is empty).
    - **2 WARNs**: `W_UNKNOWN_CAMERA` (camera outside whitelist), `W_GAP` (inter-shot gap > threshold, leaving timeline black frames).
  - **Pre-write Validation**: `generate_edl.py` always writes both the CSV and the analysis report before deciding whether to exit. Malformed outputs remain on disk for inspection. The validation summary is appended to `edl_full_report.md` under an H2 heading localized by `--lang` (`🔍 EDL Validation Result` for `en`, `🔍 EDL 驗證結果` for `zh-TW`).
  - Passing `--strict-edl` halts execution with exit code 1 if any ERROR is detected (default warns and continues).
- **Agent Policy (Dynamic Language Mirroring)**:
  - Per workspace rules, the Agent MUST pass `--lang` matching the active conversation language (e.g. `--lang zh-TW` when conversing in Traditional Chinese, `--lang en` or omit when in English; variants like `zh-Hant` or `zh_TW` automatically normalize, unsupported locales fall back to `en`).
- **Backend Architecture**:
  - **Primary**: Google Cloud Vertex AI (ADC + GCS hash-cached upload). Configure `GOOGLE_CLOUD_PROJECT` and `GCS_BUCKET` in `.env` (or pass `--project` / `--gcs-bucket`).
  - **Secondary / Backup**: Google AI Studio via `--backend studio` or automatic failover via `--fallback-studio` (`GEMINI_API_KEY`).
- **Execution Command**:
  ```bash
  # Primary with strict EDL gate & language mirroring (Recommended for Agent):
  python3 scripts/generate_edl.py -v <OUTPUT_DIR>/multicam_merged_full.mp4 \
    --strict-edl --lang zh-TW

  # With Automatic Fallback to Google AI Studio:
  python3 scripts/generate_edl.py -v <OUTPUT_DIR>/multicam_merged_full.mp4 \
    --fallback-studio --strict-edl --lang zh-TW

  # Direct Google AI Studio Execution with custom gap threshold & camera list:
  python3 scripts/generate_edl.py -v <OUTPUT_DIR>/multicam_merged_full.mp4 \
    --backend studio --strict-edl --lang zh-TW --edl-max-gap-sec 0.05 --edl-known-cameras CAM1,CAM2
  ```
- **Exit Gate 2 Verification**:
  - [x] `<OUTPUT_DIR>/edl_full.csv` (or `edl.csv`) exists and size $> 0\text{ bytes}$.
  - [x] **EDL Semantic Validation Passed**: Zero `ERROR` issues detected (`E_NO_ROWS`, `E_PARSE_TIME`, `E_NEGATIVE_DURATION`, `E_NON_MONOTONIC`, `E_OVERLAP`, `E_EMPTY_CAMERA`). Agent should pass `--strict-edl` to enforce automated hard gate rejection.
  - [x] `<OUTPUT_DIR>/edl_full_report.md` exists with cutting rationale, performance metrics, and appended validation table.
  - [x] Zero countdown residue verified in `[Start, Start+2.0s]`.
  - 🚨 *Do NOT proceed to Stage 3 until all Gate 2 criteria pass.*

---

### Stage 3A: Export NLE Timeline (⭐ Primary Path / 90% Use Case)
- **Goal**: Convert full-length EDL CSV and synchronized camera masters into standard Final Cut Pro 7 XML (`xmeml version 4`) with color decision markers.
- **Validation on Entry**: Re-validates EDL upon loading and automatically infers camera whitelist from source media directory. Supports `--strict-edl` and `--lang` (Dynamic Language Mirroring).
- **Execution Command**:
  ```bash
  # Standard export with strict validation & language mirroring:
  python3 scripts/export_fcp7_xml.py -d <OUTPUT_DIR> -o <OUTPUT_DIR>/final_cut_full.xml \
    --strict-edl --lang zh-TW
  ```
- **Exit Gate 3A Verification**:
  - [x] `<OUTPUT_DIR>/final_cut_full.xml` exists.
- **NLE Import Instructions for User**:
  1. Open DaVinci Resolve (or Premiere Pro / Final Cut Pro) and create a project.
  2. Drag all synchronized camera masters (`CAM1_synced.mp4`, `CAM2_synced.mp4`...) into the **Media Pool**.
  3. Go to **File -> Import -> Timeline...**, and select `final_cut_full.xml`.
  4. All cut points, audio tracks, and color decision markers will load instantly!

---

### Stage 3B: Direct Video Rendering (🎬 Secondary Fast Preview Path / 10% Use Case)
- **Goal**: Hardware-accelerated clip rendering directly from synchronized camera masters into full-length `final_cut_full.mp4` in a single pass.
- **Validation on Entry**: Re-validates EDL upon loading and checks camera validity against media files/camera map. Supports `--strict-edl` and `--lang` (Dynamic Language Mirroring).
- **Execution Command**:
  ```bash
  # Direct render with strict validation & language mirroring:
  python3 scripts/edl_to_video.py --edl <OUTPUT_DIR>/edl_full.csv --media-dir <OUTPUT_DIR> \
    -o <OUTPUT_DIR>/final_cut_full.mp4 --strict-edl --lang zh-TW
  ```
- **Exit Gate 3B Verification**:
  - [x] `<OUTPUT_DIR>/final_cut_full.mp4` exists with duration $> 0$.

---

### Stage 4: YouTube Subtitles Generation (📝 On-Demand / Subtitle Requests)
- **Goal**: Three-Stage Golden Pipeline: Gemini 1M Context Global Audio Glossary Extraction + Local Whisper Zero-Drift Physical Timestamps + Multimodal Audio-Text Precision Proofreading.
- **Backend Architecture**:
  - **Primary**: Google Cloud Vertex AI (ADC + inline chunk audio / GCS for audio >20MB).
  - **Secondary / Backup**: Google AI Studio via `--backend studio` or `--fallback-studio` (`GEMINI_API_KEY`).
- **Execution Command**:
  ```bash
  # Standard Execution (Google Cloud Vertex AI with ADC):
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4

  # With Automatic Fallback to Google AI Studio if GCP credentials encounter errors:
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4 --fallback-studio

  # Direct Google AI Studio Execution:
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4 --backend studio

  # If user provided interview outline / guest notes (Optional Outline Injection):
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4 --outline "<OUTLINE_TEXT_OR_FILE>"

  # If user provided full recording script / manuscript (Ground Truth Manuscript Anchor):
  python3 scripts/generate_subtitles.py -i <OUTPUT_DIR>/final_cut_full.mp4 --script "<SCRIPT_TEXT_OR_FILE>"
  ```
- **Exit Gate 4 Verification**:
  - [x] `<OUTPUT_DIR>/final_cut_full.srt` and `<OUTPUT_DIR>/final_cut_full.vtt` exist and contain millisecond-accurate corrected subtitles.
  - [x] `<OUTPUT_DIR>/final_cut_full_glossary.md` exists with global terminology rules.
  - [x] `<OUTPUT_DIR>/final_cut_full_subtitle_report.md` exists with Netflix/YouTube pacing audit and actionable review list.
