# Multi-Camera AI Preprocessing & Editing Suite - Workspace Rules (GEMINI.md)

This file defines the highest-priority always-on operational rules for the Google Antigravity Agent when working in this workspace.

---

## 🛑 Core Invariant Constraints

1. **Strict Toolset Execution Only (No Ad-Hoc Scripts)**:
   - All tasks MUST be executed through the standard scripts in `scripts/`. Writing temporary Python scripts, ad-hoc algorithms, or custom synchronization code is **STRICTLY FORBIDDEN**.
2. **Mandatory Workflow Adherence**:
   - The Agent MUST execute multi-camera tasks strictly following the 4-Stage Gated Workflow defined in [multicam_workflow.md](file:///.agent/workflows/multicam_workflow.md).
   - Never skip steps. If Step 1 produces multiple parts, Step 2 MUST run `generate_edl.py` for EVERY part file.
3. **Verification Before Completion**:
   - Do NOT declare task completion until all exit criteria for Step 3A (`final_cut_full.xml`) or Step 3B (`final_cut_full.mp4`) and Step 4 (`final_cut_full.srt` / `.vtt`) have passed verification.

---

## 🗣️ User Communication Standard

- **Dynamic Language Mirroring**: The Agent MUST detect and respond in the user's prompt language (Traditional Chinese by default when prompted in Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.).
- **Concise & Goal-Oriented Notifications**: Keep stage updates brief, natural, and goal-oriented:
  - Step 1: "🎬 正在同步多機位音訊與切分章節..."
  - Step 2: "🤖 正在進行 AI 鏡頭剪輯分析..."
  - Step 3A: "📁 正在匯出剪輯時間線 (XML)..."
  - Step 3B: "🎬 正在渲染影片成片..."
  - Step 4: "📝 正在製作字幕..."
