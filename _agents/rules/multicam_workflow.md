---
trigger: always_on
description: "Mandatory 4-stage gated workflow for multi-camera video preprocessing, AI rough-cut, XML export, and subtitles."
---

# Multi-Camera Gated Pipeline Rules (Antigravity Always-On Rules)

When the user requests processing, syncing, editing, or generating subtitles for multi-camera video footage, the Agent MUST execute the workflow strictly adhering to the 4 Stage Gates below:

## 🔒 Stage Gate Assertions

1. **Gate 1 Check (Step 1 -> Step 2)**:
   - Before calling `scripts/generate_edl.py`, verify that `multicam_sync.json` exists and at least one `multicam_merged_part*.mp4` is present in the output directory.
   - If missing, re-run `scripts/multicam_pipeline.py`.

2. **Gate 2 Check (Step 2 -> Step 3)**:
   - Scan all `multicam_merged_part*.mp4` generated in Step 1.
   - Ensure `scripts/generate_edl.py` is executed for EVERY part file.
   - Verify that all corresponding `edl_part*.csv` files exist and are non-empty (>0 bytes).

3. **Gate 3A Check (Step 3A Timeline Export)**:
   - Verify `final_cut_full.xml` exists in output directory before concluding NLE workflow.

4. **Gate 3B / Gate 4 Check (Step 3B Render & Step 4 Subtitles)**:
   - If rendering video, verify `final_cut_full.mp4` exists before calling `generate_subtitles.py`.
   - Verify `final_cut_full.srt` and `.vtt` exist before concluding subtitle workflow.

## 🗣️ User Communication Standard

- **Dynamic Language Mirroring**: The Agent MUST dynamically detect and respond in the language used by the user (e.g. Traditional Chinese when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese).
- **Concise & Goal-Oriented Notifications**: Keep stage updates brief, natural, and goal-oriented (avoid low-level technical jargon):
  - **Traditional Chinese (繁體中文)**:
    - Step 1: "🎬 正在同步多機位音訊與切分章節..."
    - Step 2: "🤖 正在進行 AI 鏡頭剪輯分析..."
    - Step 3A: "📁 正在匯出剪輯時間線 (XML)..."
    - Step 3B: "🎬 正在渲染影片成片..."
    - Step 4: "📝 正在製作字幕..."
  - **English**:
    - Step 1: "🎬 Syncing multicam audio and segmenting chapters..."
    - Step 2: "🤖 Analyzing AI rough-cut camera angles..."
    - Step 3A: "📁 Exporting editing timeline (XML)..."
    - Step 3B: "🎬 Rendering final edited video..."
    - Step 4: "📝 Generating subtitles..."
