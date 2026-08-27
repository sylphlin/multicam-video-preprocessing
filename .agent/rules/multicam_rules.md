---
trigger: always_on
description: "Mandatory constraints, stage assertions, and communication guidelines for multi-camera video projects."
---

# Multi-Camera AI Preprocessing & Editing Rules

When operating in this workspace, the Agent MUST strictly adhere to the following rules:

## 🛑 Core Constraints

1. **Strict Toolset Execution Only**:
   - All tasks MUST be executed through standard modular scripts in `scripts/`. Writing temporary Python scripts or ad-hoc algorithms is strictly prohibited.
2. **No Step Skipping**:
   - Must strictly follow the 4-Stage Gated Workflow sequentially (Step 1 -> Step 2 -> Step 3A/3B -> Step 4).
   - If Step 1 produces multiple parts, Step 2 MUST run `generate_edl.py` for EVERY part file.
3. **No Premature Task Completion**:
   - Do not conclude the task until output assertions for Step 3A (`final_cut_full.xml`) or Step 3B (`final_cut_full.mp4`) and Step 4 (`final_cut_full.srt` / `.vtt`) have passed.

## 🗣️ User Communication Standard

- **Dynamic Language Mirroring**: The Agent MUST detect and respond in the user's prompt language (Traditional Chinese by default when prompted in Chinese, English when prompted in English, etc.).
- **Concise & Goal-Oriented Notifications**: Keep stage updates brief, natural, and goal-oriented:
  - Step 1: "🎬 正在同步多機位音訊與切分章節..."
  - Step 2: "🤖 正在進行 AI 鏡頭剪輯分析..."
  - Step 3A: "📁 正在匯出剪輯時間線 (XML)..."
  - Step 3B: "🎬 正在渲染影片成片..."
  - Step 4: "📝 正在製作字幕..."
