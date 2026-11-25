---
trigger: always_on
---
# Multi-Camera AI Preprocessing & Editing Suite — Workspace & Development Rules

This file defines the authoritative rules for AI Coding Agents working in this repository. It covers both **Client Execution Invariants** (when running the multi-camera pipeline for end users) and **Repository Engineering Standards** (when developing, maintaining, or extending this project).

---

## Part I: Operational Invariants (When Executing Multi-Camera Tasks)

1. **Strict Toolset Execution Only (No Ad-Hoc Scripts)**:
   - Execute all video preprocessing, EDL rough-cutting, XML exporting, and rendering exclusively via the official scripts in `skills/multicam-video-preprocessing/scripts/`. Writing temporary Python scripts or custom audio/video synchronization logic is **STRICTLY FORBIDDEN**.
2. **Mandatory 3-Stage Gated Workflow**:
   - Follow the 3-Stage Gated Runbook defined in `skills/multicam-video-preprocessing/SKILL.md`:
     - **Stage 1**: `skills/multicam-video-preprocessing/scripts/multicam_pipeline.py --normalize --merge` (MFCC `<0.125ms` sync + EBU R128 `-14 LUFS` + synced masters + `multicam_merged_full.mp4` at `10 fps`, `1.2 Mbps`, `-g 10`).
     - **Stage 2**: `skills/multicam-video-preprocessing/scripts/generate_edl.py` (Vertex AI Gemini 4 Hydrogen Standard Multimodal `MEDIA_RESOLUTION_LOW` + dynamic `thinking_level`, automatic silence-aware 30-40 min smart segmentation with `finally` cleanup + 8-check deterministic EDL semantic validation).
     - **Stage 3A (Primary 90%)**: `skills/multicam-video-preprocessing/scripts/export_fcp7_xml.py` (`final_cut_full.xml` for DaVinci Resolve / Premiere Pro / Final Cut Pro).
     - **Stage 3B (Secondary 10%)**: `skills/multicam-video-preprocessing/scripts/edl_to_video.py` (`final_cut_full.mp4` single-pass hardware render).
3. **Fail-Fast & Exit Gate Verification**:
   - If any script exits with a non-zero status (e.g., missing ADC credentials, 403/401 GCS/Vertex AI permission error, or `--strict-edl` validation failure), stop immediately, report the exact error, and instruct the user to run `./setup.sh --project YOUR_PROJECT_ID` or `gcloud auth application-default login`.
   - Never declare completion until all required stage output files exist on disk and are non-empty (`> 0 bytes`).
4. **Dynamic Language Mirroring, Live Progress Updates & Proactive MP4 Render Offer**:
   - Detect and respond in the user's prompt language (Traditional Chinese `zh-TW` by default when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.) and pass the corresponding `--lang` parameter to CLI scripts.
   - Pass `NotificationTimeoutSeconds=60` on `run_command` for long-running stages (`multicam_pipeline.py`, `generate_edl.py`, `edl_to_video.py`). Whenever a command is still running after 60s, report the latest `[Stage N - Step X/Y]` progress to the user and schedule a 60s follow-up check (`TimerCondition="<task-id>"`).
   - When finishing at **Stage 3A (`final_cut_full.xml`)**, always explicitly inform the user that you can also render a directly playable multi-camera rough-cut video (`final_cut_full.mp4`) via **Stage 3B (`edl_to_video.py`)** upon request.

---

## Part II: Repository Development & Engineering Standards (When Developing This Project)

When modifying code, prompts, infrastructure scripts, or documentation in this repository, you MUST adhere to the following engineering standards:

### 1. Single Source of Truth (SSOT) Directory Architecture
- **Canonical Code Location**: All core scripts (`scripts/*.py`), modules (`scripts/modules/*.py`), and prompt templates (`assets/*.md`) reside inside `skills/multicam-video-preprocessing/scripts/` and `skills/multicam-video-preprocessing/assets/`.
- **Rule**: Always edit files under `skills/multicam-video-preprocessing/scripts/` and `skills/multicam-video-preprocessing/assets/`. Do not create root-level symlinks or duplicate physical directories at the repository root.

### 2. 100% Google Cloud Vertex AI (ADC) + GCS Architecture
- **Zero API Key Policy**: All Gemini model invocations in `skills/multicam-video-preprocessing/scripts/modules/llm_client.py` and `skills/multicam-video-preprocessing/scripts/modules/gcp_client.py` MUST use `genai.Client(vertexai=True, project=..., location=...)` authenticated via Application Default Credentials (ADC), defaulting to `GOOGLE_CLOUD_LOCATION=global`.
- **No AI Studio Dependencies**: Do NOT re-introduce `GEMINI_API_KEY`, AI Studio File API (`generativelanguage.googleapis.com`), `--backend studio`, or `--fallback-studio`.
- **GCS Infrastructure & Two-Tier Lifecycle (`setup.sh`)**:
  - All cloud environment setup must be consolidated in `setup.sh` using 100% native `gcloud` CLI commands (`deploy.sh` is reserved for future Gemini Enterprise deployment).
  - Bucket Lifecycle auto-cleanup rules must maintain the two-tier retention policy:
    - **`raw/` prefix**: **2 days (`age: 2`)** for ephemeral staging videos (with local SHA-256 hash caching). Temporary chunk slices under `<output_dir>/_edl_chunks/` and `gs://<bucket>/raw/edl_chunks/` are deleted immediately in `finally` blocks after Stage 2 completes.
    - **`output/`, `deliverables/`, `multicam_assets/` prefixes**: **15 days (`age: 15`)** for deliverables retention.

### 3. Deterministic Validation & Unit Testing Gate
- **Mandatory Test Execution**: Before committing any change to `scripts/` or `scripts/modules/`, run the complete unit test suite and ensure 100% pass rate:
  ```bash
  python3 -m unittest discover -s tests -v
  ```
- **Validator Invariants**:
  - Any modification to EDL generation or parsing must preserve all 8 structural checks (`6 ERROR + 2 WARN`) in `skills/multicam-video-preprocessing/scripts/modules/edl_validator.py` and ensure `generate_edl.py` writes CSV/Markdown outputs to disk *before* exiting on `--strict-edl`.

### 4. Antigravity Plugin Architecture, 5-Language Parity, & Commit Policy
- **Plugin Structure**: Keep `plugin.json`, `rules/AGENTS.md`, `.agents/rules/DEVELOPMENT.md`, and `skills/multicam-video-preprocessing/SKILL.md` synchronized at all times.
- **Multilingual README Synchronization**: Whenever CLI options, cloud setup (`setup.sh`), lifecycle rules, or workflow behaviors change, you MUST synchronously update all 5 language READMEs (`README.zh-TW.md`, `README.md`, `README.zh-CN.md`, `README.ja.md`, `README.ko.md`).
- **Human Authorship Only**: Author all commits using the repository's configured human git user (`sylphlin`) with zero AI assistant branding or `Co-Authored-By` trailers.
