# Multi-Camera AI Preprocessing & Editing Suite — Workspace & Development Rules (AGENTS.md)

This file defines the authoritative rules for AI Coding Agents (Google Antigravity / Jetski, Claude Code, Codex, etc.) working in this repository. It covers both **Client Execution Invariants** (when running the multi-camera pipeline for end users) and **Repository Engineering Standards** (when developing, maintaining, or extending this project).

---

## Part I: Operational Invariants (When Executing Multi-Camera Tasks)

1. **Strict Toolset Execution Only (No Ad-Hoc Scripts)**:
   - Execute all video preprocessing, EDL rough-cutting, XML exporting, rendering, and subtitle generation exclusively via the official scripts in `scripts/`. Writing temporary Python scripts or custom audio/video synchronization logic is **STRICTLY FORBIDDEN**.
2. **Mandatory 4-Stage Gated Workflow (Zero-Split Agentic Architecture)**:
   - Follow the 4-Stage Gated Runbook defined in [SKILL.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/SKILL.md):
     - **Stage 1**: `scripts/multicam_pipeline.py --normalize --merge` (MFCC `<0.125ms` sync + EBU R128 `-14 LUFS` + synced masters + `multicam_merged_full.mp4`).
     - **Stage 2**: `scripts/generate_edl.py` (Vertex AI Gemini 3.8 Flash Agentic Video `processing="agentic"` on full-length grid video + 8-check deterministic EDL semantic validation).
     - **Stage 3A (Primary 90%)**: `scripts/export_fcp7_xml.py` (`final_cut_full.xml` for DaVinci Resolve / Premiere Pro / Final Cut Pro).
     - **Stage 3B (Secondary 10%)**: `scripts/edl_to_video.py` (`final_cut_full.mp4` single-pass hardware render).
     - **Stage 4**: `scripts/generate_subtitles.py` (Three-Stage Golden Pipeline: Vertex AI 1M Glossary + Whisper Word Timestamps + Vertex AI Chunked Multimodal Audio Proofreading).
3. **Fail-Fast & Exit Gate Verification**:
   - If any script exits with a non-zero status (e.g., missing ADC credentials, 403/401 GCS/Vertex AI permission error, or `--strict-edl` validation failure), stop immediately, report the exact error, and instruct the user to run `./setup.sh --project YOUR_PROJECT_ID` or `gcloud auth application-default login`.
   - Never declare completion until all required stage output files exist on disk and are non-empty (`> 0 bytes`).
4. **Dynamic Language Mirroring**:
   - Detect and respond in the user's prompt language (Traditional Chinese `zh-TW` by default when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.) and pass the corresponding `--lang` parameter to CLI scripts.

---

## Part II: Repository Development & Engineering Standards (When Developing This Project)

When modifying code, prompts, infrastructure scripts, or documentation in this repository, you MUST adhere to the following engineering standards:

### 1. Single Source of Truth (SSOT) & Symlink Integrity
- **Canonical Code Location**: All core scripts (`scripts/*.py`), modules (`scripts/modules/*.py`), and prompt templates (`assets/*.md`) reside inside `skills/multicam-video-preprocessing/scripts/` and `skills/multicam-video-preprocessing/assets/`.
- **Root Symlinks**: Top-level `scripts` and `assets` at the repository root are POSIX symlinks pointing to `skills/multicam-video-preprocessing/scripts` and `skills/multicam-video-preprocessing/assets`.
- **Rule**: Always edit files under `skills/multicam-video-preprocessing/scripts/` and `skills/multicam-video-preprocessing/assets/`. Never replace root symlinks with duplicate physical directories.

### 2. 100% Google Cloud Vertex AI (ADC) + GCS Architecture
- **Zero API Key Policy**: All Gemini model invocations in [llm_client.py](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/scripts/modules/llm_client.py) and [gcp_client.py](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/scripts/modules/gcp_client.py) MUST use `genai.Client(vertexai=True, project=..., location=...)` authenticated via Application Default Credentials (ADC), defaulting to `GOOGLE_CLOUD_LOCATION=global`.
- **No AI Studio Dependencies**: Do NOT re-introduce `GEMINI_API_KEY`, AI Studio File API (`generativelanguage.googleapis.com`), `--backend studio`, or `--fallback-studio`.
- **GCS Infrastructure & Two-Tier Lifecycle (`setup.sh`)**:
  - All cloud environment setup must be consolidated in [setup.sh](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/setup.sh) using 100% native `gcloud` CLI commands (`deploy.sh` is reserved for future Gemini Enterprise deployment).
  - Bucket Lifecycle auto-cleanup rules must maintain the two-tier retention policy:
    - **`raw/` prefix**: **2 days (`age: 2`)** for ephemeral staging videos/audio (with local SHA-256 hash caching).
    - **`output/`, `deliverables/`, `multicam_assets/` prefixes**: **15 days (`age: 15`)** for deliverables retention.
    - **`raw/audio_chunks/`**: Immediate deletion in `finally` blocks after each subtitle proofreading chunk completes.

### 3. Deterministic Validation & Unit Testing Gate
- **Mandatory Test Execution**: Before committing any change to `scripts/` or `scripts/modules/`, run the complete unit test suite and ensure 100% pass rate:
  ```bash
  python3 -m unittest discover -s tests -v
  ```
- **Validator & Audit Invariants**:
  - Any modification to EDL generation or parsing must preserve all 8 structural checks (`6 ERROR + 2 WARN`) in [edl_validator.py](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/scripts/modules/edl_validator.py) and ensure `generate_edl.py` writes CSV/Markdown outputs to disk *before* exiting on `--strict-edl`.
  - Any modification to subtitle generation must preserve the 8-dimension Netflix/YouTube streaming quality audit in [generate_subtitles.py](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/scripts/generate_subtitles.py).

### 4. Antigravity Plugin Architecture & 5-Language Documentation Parity
- **Plugin Structure**: Keep [plugin.json](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/plugin.json), [rules/AGENTS.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/rules/AGENTS.md), and [skills/multicam-video-preprocessing/SKILL.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/skills/multicam-video-preprocessing/skills/multicam-video-preprocessing/SKILL.md) synchronized at all times. Do not use deprecated `.agent/workflows/` or `.agent/rules/` folders.
- **Multilingual README Synchronization**: Whenever CLI options, cloud setup (`setup.sh`), lifecycle rules, or workflow behaviors change, you MUST synchronously update all 5 language READMEs:
  1. [README.zh-TW.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/README.zh-TW.md) (Traditional Chinese)
  2. [README.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/README.md) (English)
  3. [README.zh-CN.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/README.zh-CN.md) (Simplified Chinese)
  4. [README.ja.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/README.ja.md) (Japanese)
  5. [README.ko.md](file:///Users/sylph/Documents/Antigravity/multicam-video-preprocessing/README.ko.md) (Korean)
