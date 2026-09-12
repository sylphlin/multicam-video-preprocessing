---
trigger: always_on
description: "Mandatory constraints, execution policies, and communication rules for multi-camera video preprocessing and AI editing."
---

# Multi-Camera Workspace Rules

When processing multi-camera projects, the Agent MUST adhere to the following rules:

1. **Strict Toolset Execution**:
   - Execute all operations via official modular scripts in `scripts/`. Ad-hoc temporary scripts are strictly prohibited.
2. **Sequential Workflow Execution (Zero-Split Agentic Architecture)**:
   - Follow the 4-stage sequence defined in `.agent/workflows/multicam_workflow.md` (Stage 1 -> Stage 2 -> Stage 3A/3B -> Stage 4).
   - The primary pipeline operates on full-length footage using Gemini 3.8 Flash Agentic Video Understanding, eliminating chapter splitting.
3. **Strict Exit Gate Verification**:
   - Verify all required output files exist and are non-empty before concluding any stage.
4. **Dynamic Language Mirroring**:
   - Respond in the user's prompt language with concise, goal-oriented stage updates.
