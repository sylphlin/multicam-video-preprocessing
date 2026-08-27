# 多機位影片智慧處理與 AI 剪輯套件 (Multicam Video Pipeline & AI Editing Suite)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 專用原生技能與工作流 (Antigravity Native Skill & Workflow)**  
> 本工具套件是專為 **Google Antigravity Agent 架構（基於 Gemini 3.7 Flash 1M 多模態長上下文）** 與專業剪輯軟體（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）量身打造的原生多機位（2~6 機）智慧處理管線。

---

本專案將複雜的多機位影音工程封裝為 **4 階段嚴格閘門工作流 (4-Stage Gated Workflow)**，涵蓋 FFT 物理聲學時間對齊、EBU R128 廣播級音量標準化、30~40 分鐘自然停頓切分、多合一緊湊網格畫面合成、Gemini 3.7 Flash 多模態粗剪決策、FCP7 XML 時間線匯出，以及高精度 YouTube 字幕製作。

---

## 📦 Antigravity 匯入與結構 (Installation & Setup)

本專案完全適配 Antigravity Skill 與 Workflow 標準結構，可直接 Clone 至 Antigravity 技能目錄下無縫啟用：

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 套件檔案結構
```text
multicam-video-preprocessing/
├── GEMINI.md                          # Antigravity 根目錄常駐工作區規則
├── .agent/
│   ├── rules/
│   │   └── multicam_rules.md          # 常駐紀律規則 (Always-On Rules)
│   └── workflows/
│       └── multicam_workflow.md       # 官方 4 階段執行工作流 (Stage-Gated Runbook)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity 技能能力定義清單
├── assets/                            # 提示詞樣板資產
│   ├── edl_interview_template.md      # Gemini 訪談粗剪提示詞樣板
│   └── subtitle_proofread_template.md # YouTube 字幕語意校對樣板
├── scripts/                           # 核心執行工具庫
│   ├── multicam_pipeline.py           # 步驟 1: 多機時間同步、音量標準化、分段與網格合成
│   ├── generate_edl.py                # 步驟 2: Gemini 3.7 Flash 多模態剪輯決策
│   ├── export_fcp7_xml.py             # 步驟 3A: 匯出 FCP7 XML 剪輯時間線 (主路徑)
│   ├── edl_to_video.py                # 步驟 3B: 直接渲染分段成片 (次路徑)
│   ├── concat_videos.py               # 步驟 3B: 全集章節無損拼接 (次路徑)
│   ├── generate_subtitles.py          # 步驟 4: YouTube 字幕生成 (Whisper+Gemini)
│   └── modules/                       # 核心聲學與視訊演算法模組
└── README.zh-TW.md
```

---

## 🌟 端到端全流程架構 (Full End-to-End Workflow)

```mermaid
flowchart TD
    subgraph S1["步驟 1：多機前處理管線 (multicam_pipeline.py)"]
        A["多機位原始素材 (CAM1, CAM2...)"] --> S1_1["1.1 8kHz FFT 音訊時間線對齊 (計算 Δt)"]
        S1_1 --> S1_2["1.2 EBU R128 音量標準化 (-14 LUFS)"]
        S1_2 --> S1_3["1.3 導出全集同步母帶 (CAM*_synced.mp4)"]
        S1_3 --> S1_4["1.4 自然停頓點章節切分 (Part 1, Part 2...)"]
        S1_4 --> S1_5["1.5 多合一網格畫面合成 (multicam_merged_part*.mp4)"]
    end

    S1_5 --> S2["步驟 2：Gemini AI 多模態粗剪決策 (generate_edl.py)"]
    S2 --> EDL["EDL 剪輯決策列表 (edl_part*.csv)"]

    subgraph S3A["主路徑：專業剪輯 (90%)"]
        S1_3 --> S3A_ACT["步驟 3A：匯出 FCP7 XML 時間線 (export_fcp7_xml.py)"]
        EDL --> S3A_ACT
        S3A_ACT --> XML["final_cut_full.xml<br/>(無縫導入 DaVinci Resolve / Premiere Pro / Final Cut Pro)"]
    end

    subgraph S3B["次路徑：直接成片與字幕 (10%)"]
        S1_3 --> S3B_ACT["步驟 3B：直接渲染與無損拼接 (edl_to_video.py + concat)"]
        EDL --> S3B_ACT
        S3B_ACT --> MP4["final_cut_full.mp4"]
        MP4 --> S4["步驟 4：YouTube 字幕生成 (generate_subtitles.py)"]
        S4 --> SRT["final_cut_full.srt / .vtt"]
    end
```

---

## 🛠️ 環境需求

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg**（支援 `h264_videotoolbox` 硬體編碼與 `loudnorm` 濾鏡）
- **Python 3.8+**
- **NumPy** (`pip install numpy`)

---

## 🚀 完整執行指令指南

### 方案 A：專業剪輯工作流（匯出 XML 導入 DaVinci / Premiere ⭐ 推薦）

```bash
# 1. 多機位前處理（同步對齊 + 音量標準化 + 停頓切分 + 導出同步母帶 + 網格合成）
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --split-min-dur 30 --split-max-dur 40 \
  --normalize --merge \
  -o ./output/

# 2. Gemini 3.7 Flash AI 多模態粗剪決策（針對每個 Part 執行）
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3A. 匯出 FCP7 XML 剪輯時間線
python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml
```

#### 🎬 DaVinci Resolve 導入步驟：
1. 打開 DaVinci Resolve 並新建專案。
2. 將 `./output/CAM1_synced.mp4` 與 `./output/CAM2_synced.mp4` 拖入**媒體池 (Media Pool)**。
3. 點選 **檔案 $\\rightarrow$ 導入 $\\rightarrow$ 時間線...** (`Ctrl+Shift+I` / `Cmd+Shift+I`)，選擇 `final_cut_full.xml`。
4. 全片所有鏡頭切點、主音訊軌與彩色 Marker 標記瞬間載入就緒！

---

### 方案 B：命令列直接成片渲染（快速預覽 🎬）

```bash
# 1. 多機位前處理（同方案 A）
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --normalize --merge -o ./output/

# 2. Gemini AI 粗剪決策（同方案 A）
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3B. 渲染各分段成片並無損合併
python3 scripts/edl_to_video.py --edl ./output/edl_part1.csv
python3 scripts/edl_to_video.py --edl ./output/edl_part2.csv
python3 scripts/concat_videos.py -d ./output/ -o ./output/final_cut_full.mp4

# 4. 生成 YouTube 字幕（Whisper 轉錄 + Gemini 語意校對）
python3 scripts/generate_subtitles.py -i ./output/final_cut_full.mp4
```

---

## ⚙️ CLI 參數速查表 (`multicam_pipeline.py`)

| 參數 | 說明 | 預設值 |
| :--- | :--- | :--- |
| `--ref` | 基準攝影機影片路徑 (CAM1) | *必填* |
| `--targets` / `--target` | 1~5 支目標攝影機影片路徑（支援 2~6 機） | *必填* |
| `--auto-split` | 啟用 30~40 分鐘自然停頓章節切分 | `False` |
| `--split-min-dur` | 切分片段最小時長 (分鐘) | `30.0` |
| `--split-max-dur` | 切分片段最大時長 (分鐘) | `40.0` |
| `--merge` / `--multi-in-one` | 渲染多合一網格影片（節省 50%~83% Token） | `False` |
| `--encoder` | 視訊編碼器 (`h264_videotoolbox` / `libx264`) | `h264_videotoolbox` |
| `--normalize` | 啟用 EBU R128 (-14 LUFS) 廣播級音量標準化 | `False` |
| `-o` / `--output-dir` | 同步母帶、網格影片與報告輸出目錄 | `.` (當前目錄) |
| `--suffix` | 同步母帶檔名後綴 | `_synced` |
