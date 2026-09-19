# Multi-Camera Video Pipeline & AI Editing Suite

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **Google Antigravity 原生技能与工作流套件**  
> 本工具集为 **Google Antigravity**（由 **Vertex AI Gemini 3.8 Flash** 驱动）与专业非线性剪辑软件（**DaVinci Resolve**、**Adobe Premiere Pro**、**Final Cut Pro**）打造四阶段多机位预处理与 AI 粗剪工作流。

---

**Multi-Camera Video Pipeline & AI Editing Suite** 支持 2 至 6 机位音频对齐、广播级响度标准化、AI 粗剪时间线生成与 YouTube 字幕校对。可在 Antigravity 对话窗口使用自然语言下达指令，或通过终端 CLI 运行。

---

## 安装与 Google Cloud 环境配置 (`setup.sh`)

本项目遵循 [Agent Plugins 1.0](https://agent-plugins.org/) 规范，完全基于 **Google Cloud Vertex AI (ADC)** 与 **Cloud Storage (GCS)** 运行，无需 API Key。

```bash
# 1. 安装为全局 Antigravity Plugin
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/plugins/multicam-video-preprocessing

# 2. 安装依赖与授权 ADC
brew install ffmpeg
pip install numpy google-genai google-cloud-storage mlx-whisper requests
gcloud auth application-default login

# 3. 运行 setup.sh 配置 GCS 存储桶、双层生命周期规则（raw: 2 天，交付物: 15 天）、IAM 与 .env
chmod +x setup.sh
./setup.sh --project YOUR_GCP_PROJECT_ID
```

---

## 四阶段核心流程与 CLI 命令

### Stage 1：多机位同步与预处理 (`multicam_pipeline.py`)
1. **MFCC 声学时间对齐与亚帧微调（`<0.125 ms`）**：支持三阶扫描与 `--strict-sync` 低置信度拦截。
2. **EBU R128 (`-14 LUFS`) 双阶段线性响度标准化**：锁定 `-14.0 LUFS` 并消除动态压缩痕迹。
3. **逐帧精准同步母带导出 (`CAM*_synced.mp4`)**：使用硬件加速重编码避免关键帧偏移。
4. **零切分全长网格合成 (`multicam_merged_full.mp4`)**：将 2 至 6 机位合成为单一多视角画布（$\le 1920 \times 1080$）。

```bash
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 CAM3.mp4 \
  --normalize --merge -o output/
```

### Stage 2：Gemini 3.8 Flash Agentic 粗剪决策 (`generate_edl.py`)
使用 **Vertex AI Gemini 3.8 Flash**（`processing="agentic"`）对全长网格视频生成 `edl_full.csv`，并执行 8 项确定性语义检查（`6 ERROR + 2 WARN`）：

```bash
python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --strict-edl --lang zh-CN
```

### Stage 3A：导出 FCP7 XML 时间线 (`export_fcp7_xml.py`)
```bash
python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml --fps 29.97 --drop-frame
```

### Stage 3B：单次硬件加速视频渲染 (`edl_to_video.py`)
```bash
python3 scripts/edl_to_video.py --edl output/edl_full.csv -o output/final_cut_full.mp4 --strict-edl
```

### Stage 4：三阶段黄金字幕管线 (`generate_subtitles.py`)
结合 **Vertex AI 1M 全局术语表**、**Whisper 逐字时间戳** 与 **Gemini 多模态音频切片校对 + 8 维度流媒体质量审核**：

```bash
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --language zh-CN
```

---

## Google Drive 直连与 GCS 双层生命周期规则

| GCS 路径前缀 (`matchesPrefix`) | 存储对象 | 保留天数 (`age`) | 清理机制 |
| :--- | :--- | :--- | :--- |
| **`raw/audio_chunks/`** | Stage 4.3 音频切片 | **推理后立即删除** | 每个分块完成后在 Python `finally` 块中立即删除。 |
| **`raw/`** | 暂存网格视频与完整音轨 | **2 天 (`age: 2`)** | 保留 2 天供 SHA-256 缓存复用，期满自动删除。 |
| **`output/`**、**`deliverables/`**、**`multicam_assets/`** | XML/CSV 时间线、SRT/VTT 字幕与报告 | **15 天 (`age: 15`)** | 保留 15 天供团队审阅，期满自动清理。 |

---

## 许可证 (License)

本项目采用 [MIT License](LICENSE) 授权。
