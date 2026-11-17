---
trigger: always_on
---

# Multi-Camera Video Preprocessing - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution & Direct CLI Invocation (Do Not Modify Plugin Code)
- All Python scripts (`skills/multicam-video-preprocessing/scripts/*.py`), prompt specifications (`skills/multicam-video-preprocessing/assets/*.md`), and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with `replace_file_content`, `write_to_file`, or shell commands.
- Do NOT write ad-hoc temporary Python scripts, custom synchronization code, or one-off shell algorithms.
- Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/multicam-video-preprocessing/SKILL.md` (`../../`, e.g., `~/.gemini/config/plugins/multicam-video-preprocessing`).
- Set `Cwd` to `<PLUGIN_ROOT>` and run the official scripts (`python3 skills/multicam-video-preprocessing/scripts/...`) directly with `run_command` using the specified arguments. Do NOT search for global CLI aliases with `find_by_name` or `list_dir`.

## 2. Fail-Fast on Errors (Do Not Debug or Rewrite Code)
- If a script fails (exit code is not 0) or an external error occurs (such as 401 Unauthorized, 403 Forbidden, Quota Exceeded, missing Application Default Credentials, or missing FFmpeg):
  - Stop immediately.
  - Show the exact error message and exit status to the user.
  - Give a clear, actionable solution to the user (for example, run `./setup.sh --project YOUR_PROJECT_ID`, run `gcloud auth application-default login`, run `pip install -r requirements.txt`, or install FFmpeg).
  - Do NOT try to modify the script, probe different code paths, or rewrite logic.

## 3. Strict Zero-Emoji Policy in Technical Reports & Dynamic Language Mirroring
- Do NOT use decorative emojis or icons in generated technical EDL tables or validation Markdown reports unless defined by the official report schema.
- Keep all generated documentation and validation reports in plain, professional technical text.
- Always respond to the user in their prompt language (Traditional Chinese `zh-TW` when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.) and pass the matching `--lang` flag to validation scripts.

## 4. Acoustic Ground Truth, Live Progress & 3-Stage Pipeline Integrity
- Execute the 3-Stage Gated Workflow sequentially (`multicam_pipeline.py` -> `generate_edl.py` -> `export_fcp7_xml.py` / `edl_to_video.py`) as specified in `skills/multicam-video-preprocessing/SKILL.md`.
- Pass `NotificationTimeoutSeconds=60` on `run_command` for long-running stages (`multicam_pipeline.py`, `generate_edl.py`, `edl_to_video.py`). When notified that a command is still running, report the current `[Stage N - Step X/Y]` progress to the user and schedule a 60s follow-up check (`TimerCondition="<task-id>"`).
- All time alignments and EDL cut points must respect MFCC subframe acoustic alignment (<0.125ms) and the unified grid video (`multicam_merged_full.mp4`). Long videos (>40 min) are automatically segmented at natural silence points inside `generate_edl.py` and stitched back into `edl_full.csv`.
- Do NOT manually split footage into intermediate chapters outside `generate_edl.py` or use legacy AI Studio API keys (`GEMINI_API_KEY`).
- Verify all required stage exit criteria files exist and are non-empty (`> 0 bytes`) before proceeding to the next stage or declaring task completion.
- When finishing at Stage 3A (`final_cut_full.xml`), always explicitly inform the user in your completion message that you can also render a playable rough-cut MP4 video (`final_cut_full.mp4`) via Stage 3B (`edl_to_video.py`) if needed.
