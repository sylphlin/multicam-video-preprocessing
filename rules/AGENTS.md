# Multi-Camera Video Preprocessing - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution & Direct CLI Invocation (Do Not Modify Plugin Code)
- All Python scripts (`skills/multicam-video-preprocessing/scripts/*.py`, symlinked at `scripts/*.py`), prompt specifications (`skills/multicam-video-preprocessing/assets/*.md`, symlinked at `assets/*.md`), and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with `replace_file_content`, `write_to_file`, or shell commands.
- Do NOT write ad-hoc temporary Python scripts, custom synchronization code, or one-off shell algorithms.
- Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/multicam-video-preprocessing/SKILL.md` (`../../`, e.g., `/Users/sylph/.gemini/config/plugins/multicam-video-preprocessing`).
- Set `Cwd` to `<PLUGIN_ROOT>` and run the official scripts (`python3 skills/multicam-video-preprocessing/scripts/...` or `python3 scripts/...`) directly with `run_command` using the specified arguments. Do NOT search for global CLI aliases with `find_by_name` or `list_dir`.

## 2. Fail-Fast on Errors (Do Not Debug or Rewrite Code)
- If a script fails (exit code is not 0) or an external error occurs (such as 401 Unauthorized, 403 Forbidden, Quota Exceeded, missing Application Default Credentials, or missing FFmpeg):
  - Stop immediately.
  - Show the exact error message and exit status to the user.
  - Give a clear, actionable solution to the user (for example, run `./setup.sh --project YOUR_PROJECT_ID`, run `gcloud auth application-default login`, or install FFmpeg).
  - Do NOT try to modify the script, probe different code paths, or rewrite logic.

## 3. Strict Zero-Emoji Policy in Technical Reports & Dynamic Language Mirroring
- Do NOT use decorative emojis or icons in generated technical EDL tables or Subtitle Audit Markdown reports unless defined by the official report schema.
- Keep all generated documentation and audit reports in plain, professional technical text.
- Always respond to the user in their prompt language (Traditional Chinese `zh-TW` when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.) and pass the matching `--lang` flag to validation scripts.

## 4. Acoustic Ground Truth & Zero-Split Pipeline Integrity
- Execute the 4-Stage Gated Workflow sequentially (`multicam_pipeline.py` -> `generate_edl.py` -> `export_fcp7_xml.py` / `edl_to_video.py` -> `generate_subtitles.py`) as specified in `skills/multicam-video-preprocessing/SKILL.md`.
- All time alignments, EDL cut points, and subtitle boundaries must respect MFCC subframe acoustic alignment (<0.125ms), Whisper word-level acoustic ground truth (`word_timestamps=True`), and the Zero-Split Agentic Video pipeline (`multicam_merged_full.mp4`).
- Never split full-length footage into intermediate chapters or use legacy AI Studio API keys (`GEMINI_API_KEY`).
- Verify all required stage exit criteria files exist and are non-empty (`> 0 bytes`) before proceeding to the next stage or declaring task completion.
