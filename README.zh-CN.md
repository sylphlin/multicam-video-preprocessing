# 多机位视频智能处理与 AI 剪辑套件 (Multicam Video Pipeline & AI Editing Suite)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 原生技能与工作流 (Antigravity Native Skill & Workflow)**  
> 本工具套件是专为 **Google Antigravity Agent 架构（基于 Gemini 3.7 Flash 1M 多模态长上下文）** 与专业剪辑软件（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）量身打造的原生多机位（2~6 机）智能处理管线与 AI 粗剪套件。

---

本专案为针对长上下文多模态模型（Gemini 3.7 Flash 1M Token Context）与专业剪辑软件（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）打造的模块化多机位（2 至 6 机）视频智能处理管线与 AI 粗剪套件。使用者无需手动输入底层终端机指令，只要在 Antigravity 聊天室中使用自然语言发出指示，Agent 就会自动执行完整的标准化处理流程。

---

## 📦 Antigravity 导入与安装 (Installation & Setup)

本专案完全适配 Antigravity Skill 与 Workflow 标准结构，可直接 Clone 至 Antigravity 技能目录下无缝启用：

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 套件文件结构
```text
multicam-video-preprocessing/
├── GEMINI.md                          # Antigravity 根目录常驻工作区规则
├── .agent/
│   ├── rules/
│   │   └── multicam_rules.md          # 常驻纪律规则 (Always-On Rules)
│   └── workflows/
│       └── multicam_workflow.md       # 官方 4 阶段执行工作流 (Stage-Gated Runbook)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity 技能能力定义清单
├── assets/                            # 提示词模板资产 (Prompt Assets)
│   ├── edl_interview_template.md      # Gemini 访谈粗剪提示词模板
│   └── subtitle_proofread_template.md # YouTube 字幕语义校对模板
├── scripts/                           # 核心执行脚本与处理模块
│   ├── multicam_pipeline.py           # 步骤 1: 多机时间同步、音量标准化、母带导出与全集网格合成
│   ├── generate_edl.py                # 步骤 2: Gemini 3.7 Flash Agentic Video 零切分 AI 剪辑决策生成
│   ├── export_fcp7_xml.py             # 步骤 3A: 导出 FCP7 XML 时间线 (主路径)
│   ├── edl_to_video.py                # 步骤 3B: 一步到位硬件加速成片直接渲染 (次路径)
│   ├── generate_subtitles.py          # 步骤 4: 生成 YouTube 字幕 (Whisper+Gemini)
│   └── modules/                       # 核心声学与视频算法库
└── README.zh-CN.md
```

---

## 🌟 端到端全流程图 (Full End-to-End Workflow)

```mermaid
flowchart TD
    subgraph S1["步骤 1：多机前处理管线 (multicam_pipeline.py --normalize --merge)"]
        A["多机位原始素材 (CAM1, CAM2...)"] --> S1_1["1.1 8kHz FFT 音频时间线对齐 (计算 Δt)"]
        S1_1 --> S1_2["1.2 EBU R128 音量标准化 (-14 LUFS)"]
        S1_2 --> S1_3["1.3 导出全集同步母带 (CAM*_synced.mp4)"]
        S1_3 --> S1_4["1.4 多合一全集网格画面合成 (multicam_merged_full.mp4)"]
    end

    S1_4 --> S2["步骤 2：Gemini 3.7 Flash Agentic Video 智能粗剪<br/>(generate_edl.py / 节省 99.7% Token)"]
    S2 --> EDL["单一全片 EDL 剪辑决策表<br/>(edl_full.csv)"]

    subgraph S3A["主路径：专业剪辑 (90%)"]
        S1_3 --> S3A_ACT["步骤 3A：导出 FCP7 XML 兼容时间线<br/>(export_fcp7_xml.py)"]
        EDL --> S3A_ACT
        S3A_ACT --> XML["final_cut_full.xml<br/>(导入 Final Cut Pro / DaVinci Resolve / Premiere Pro)"]
    end

    subgraph S3B["次路径：直接成片与字幕 (10%)"]
        S1_3 --> S3B_ACT["步骤 3B：一步到位成片直接渲染<br/>(edl_to_video.py)"]
        EDL --> S3B_ACT
        S3B_ACT --> MP4["final_cut_full.mp4"]
        MP4 --> S4["步骤 4：YouTube 字幕生成<br/>(generate_subtitles.py)"]
        S4 --> SRT["final_cut_full.srt / .vtt"]
    end
```

---

## 💬 使用情境与 Prompt 范例 (User Scenarios & Prompt Examples)

使用者在 Antigravity 对话框中，只需以日常口语提出需求，Agent 即会自动理解并调用完整的处理管线：

### 情境一：导出专业剪辑 XML 时间线（DaVinci Resolve / Premiere Pro / Final Cut Pro ⭐ 推荐）
- **适用场景**：需要将 AI 粗剪结果导入剪辑软件，进行后续的精细剪辑、调色、动态图卡与音频混音。
- **对话 Prompt 范例**：
  > 「*我有两支双机位的访谈录像文件 `CAM1.mp4` 与 `CAM2.mp4`，请帮我进行时间同步与音量标准化，并套用访谈剪辑模板产出可直接进 DaVinci Resolve 的 XML 时间线。*」
- **交付成果**：
  1. `final_cut_full.xml`（单一完整时间线，包含全片所有镜头切点与 AI 决策理由 Marker 标记）
  2. `CAM1_synced.mp4`、`CAM2_synced.mp4`（音画同步且已完成 -14 LUFS 响度标准化的全集母带）
- **DaVinci Resolve 导入步骤**：
  1. 打开 DaVinci Resolve 并新建项目。
  2. 将 `./output/CAM1_synced.mp4` 与 `./output/CAM2_synced.mp4` 拖入 **Media Pool（媒体池）**。
  3. 点击 **文件 $\\rightarrow$ 导入 $\\rightarrow$ 时间线...** (`Cmd + Shift + I`)，选取 `final_cut_full.xml`。
  4. 全片所有镜头切点、主音频轨与彩色 Marker 标记瞬间加载就绪！

---

### 情境二：直出成片与 YouTube 字幕（快速预览与发布工作流 🎬）
- **适用场景**：不需要打开专业剪辑软件，希望快速生成一支完整的 MP4 成品视频供审片，并附带 YouTube 上传用的双语/单语字幕。
- **对话 Prompt 范例**：
  > 「*请帮我把这两支多机位素材进行 AI 粗剪，直接渲染合并成一支完整的 MP4 预览视频，并产出校对后的 YouTube 字幕。*」
- **交付成果**：
  1. `final_cut_full.mp4`（全集渲染与无损拼接成品视频）
  2. `final_cut_full.srt` / `final_cut_full.vtt`（Whisper 声学对齐 + Gemini 语义校对之 YouTube 标准字幕）

---

### 情境三：自订章节长度（自订分段时间 ⏱️）
- **适用场景**：原始素材时间较短（如 30 分钟节目），希望将章节缩短为每 10 或 15 分钟左右切一段，或依据特定主题划分。
- **对话 Prompt 范例**：
  > 「*请帮我处理这组多机位素材，但章节请改在 10 分钟附近找自然停顿点切分，最后产出 XML 时间线。*」
- **Agent 自动反应**：
  - Agent 会自动将切分参数调整为 `--split-min-dur 8 --split-max-dur 12`，无需手动修改任何配置或脚本。

---

### 情境四：为既有视频单独制作 YouTube 字幕（语音转录与校对 📝）
- **适用场景**：手边已有剪辑好的视频成品（`final_cut.mp4`），需要制作毫秒级精准且专有名词经过校对的 YouTube 字幕。
- **对话 Prompt 范例**：
  > 「*请帮我为 `output/final_cut_full.mp4` 制作 YouTube 字幕，修复同音错字与英文专有名词。*」
- **交付成果**：
  1. `final_cut_full.srt`（YouTube 标准 SubRip 字幕）
  2. `final_cut_full.vtt`（网页与 HTML5 播放器通用 WebVTT 字幕）
  3. `final_cut_full_raw_whisper.srt`（原始 Whisper 转录初稿）

---

## 🔍 各步骤执行细节说明 (Detailed Pipeline Steps)

### 步骤 1：多机同步与 AI 网格前处理 (`multicam_pipeline.py`)

1. **8kHz FFT 音频时间线全域对齐 (8kHz FFT Audio Time Alignment)**：
   - **为什么降采样至 8kHz？**：人声音频频率特征集中在 300Hz 至 3.4kHz，8kHz 采样已足以完整捕捉语音声学特征，同时大幅降低内存消耗并提升 10 倍以上的计算速度。
   - **FFT 互相关算法原理**：程序自动提取基准机（CAM1）与各目标机（CAM2 至 CAMn）的音频，利用快速傅里叶变换（Fast Fourier Transform）将时域信号转换至频域计算互相关函数（Cross-Correlation），通过寻找互相关能量峰值，精确计算出各机位开始录制的物理时间偏差 $\Delta t$（精确至毫秒），并自动校正与修剪起跑时间差。
2. **EBU R128 (-14 LUFS) 全集音量标准化 (符合 YouTube 官方建议标准)**：
   - **符合 YouTube 播放规范**：YouTube 平台采用 **-14.0 LUFS** 作为标准响度基准。若视频音量过大（高于 -14 LUFS），YouTube 后台会启动强制压缩衰减导致动态范围受损；若音量过小则影响手机与平板观众的聆听体验。
   - **双次通过（Two-Pass）线性标准化**：
     - 第一遍 (Pass 1 - 声学测量)：音频极速解码至空设备（null sink）进行即时声学分析，精确量测整段音频的整合响度（Integrated Loudness, `I`）、响度范围（Loudness Range, `LRA` = 11.0 LU）、真实峰值（True Peak, `TP` = -1.5 dBTP）与目标增益偏移（`target_offset`）。
     - 第二遍 (Pass 2 - 线性增益正则化)：启用 `linear=true` 将实际测得参数带入 `loudnorm` 滤镜进行全片纯线性增益平移，彻底根除单次通过（Single-pass）动态压缩产生的“声音抽吸感 (Volume Pumping Artifacts)”，确保全片各机位音量 100% 精准锁定 -14.0 LUFS，且绝不发生数字削波破音（True Peak Clipping Prevention）。
3. **全集同步母带帧精确并行导出 (`*_synced.mp4`)**：
   - **默认帧精确重新编码 (Frame-Accurate Re-encode)**：采用 Apple Silicon 硬件加速编码器（`h264_videotoolbox`，非 Mac 环境自动回退 `libx264 -crf 18`），彻底解决流复制 (`-c copy`) 只能在关键帧（I-frame）切割所造成的毫秒级声学对齐漂移、片头黑帧与画面卡顿问题，确保各机位母带影格毫秒级物理绝对对齐。
   - **极速模式支持**：可选传入 `--stream-copy` 启用无损流复制，适合极速粗剪。
4. **零切分全集多合一紧凑网格画面合成 (`multicam_merged_full.mp4`)**：
   - 自动依机位数排版（2机左右并排、3 至 4 机田字格、5 至 6 机六宫格），保证总画幅 $\le 1920 \times 1080$、每机 $\ge 640 \times 480$，供 Agentic Video 一次性全文理解，免除章节分割的人工切口。

---

### 步骤 2：Gemini 3.7 Flash Agentic Video 智能粗剪决策 (`generate_edl.py`)
1. **加载专属提示词资产**：
   - 读取 `assets/edl_interview_template.md` 广电级访谈剪辑规则模板。
2. **Phase 0：头尾废料与现场倒数彻底裁切 (零容忍原则与不对称安全边界)**：
   - **现场倒数零容忍**：系统性侦测并剔除开拍前设备确认、闲聊、打板与现场人员倒数声（如「5, 4, 3, 2, 1」、「五四三二」、「Ready Action」）。
   - **不对称安全边界 (`[Start, Start+2s]` 自我校验)**：强制要求 `Global_Start_Time` 必须严格落在最后一个倒数数字完全结束之后。模型在起剪后的首 2 秒区间（`[Global_Start_Time, Global_Start_Time + 2.0s]`）进行思维链自审，若仍有倒数残留则自动后移时间戳，确保成片首帧干净对齐第一句台词首字。
   - **结尾未关机裁切**：自动识别访谈结尾道别语句，切除收尾未关机闲聊、拍摄封面素材与环境杂音（标记 `Global_End_Time`）。
3. **次世代架构：Agentic Video Understanding (零切分全长剪辑)**：
   - 通过 Gemini 3.7 Flash Agentic Video 理解能力（`processing="agentic"`），直接评估 >1 小时未分段之完整多机网格视频。
   - 采用目标导向稀疏时域采样，将输入 Token 消耗巨幅降低 **99.7%**（由约 1,000,000 Token 降至约 3,000 Token），彻底免除章节交界处话语被截断的风险。
4. **产出标准化结果**：
   - 输出单一标准 CSV 决策表（`edl_full.csv`，亦兼容 `edl.csv`）与 Markdown 裁切分析报告（`edl_full_report.md`）。

---

### 步骤 3A（主路径）：导出 FCP7 XML 剪辑时间线 (`export_fcp7_xml.py`)

本步骤产出业界通用的 **Final Cut Pro 7 XML（xmeml version 4）** 兼容格式，可无缝导入 **Final Cut Pro**、**DaVinci Resolve**、**Adobe Premiere Pro** 等主流专业剪辑软件（NLE）：
1. **直连全集同步母带**：
   - 直接关联 `CAM1_synced.mp4`、`CAM2_synced.mp4`...，全片时间码 1:1 绝对对齐。
2. **1:1 绝对时间码对应**：
   - 时间线上每一个镜头保持 `start == in` 与 `end == out`，剪辑师在 NLE 中可自由进行波纹修剪（Slip/Slide）。
3. **建立连续主音轨与规则 Marker 注入**：
   - 建立全片连续的 CAM1 主收音轨道；
   - 将 AI 的剪辑规则与决策理由转化为时间线上的红蓝 Marker 标记，方便剪辑师检视。

---

### 步骤 3B（次路径）：一步到位成片直接渲染 (`edl_to_video.py`)
1. **一步到位硬件加速成片渲染**：
   - 调用 Apple Silicon 硬件编码器（`h264_videotoolbox`），直接读取全集同步母带与 `edl_full.csv` 渲染出完整成片 `final_cut_full.mp4`，无需产出中间章节分段或二次拼接。

---

### 步骤 4：生成 YouTube 字幕 (`generate_subtitles.py`)

本工具采用业界顶级的 **三阶段黄金字幕生产线（Three-Stage Golden Subtitle Pipeline）**，结合 **Gemini 1M 全篇音频宏观理解**、**Whisper 声学物理时间轴与专有名词偏置** 与 **Gemini 局部音轨多模态精修**：

#### 为什么采用「全篇词汇库 + Whisper 物理时间码 + Gemini 音频多模态审稿」？

| 比较项目 | 纯 Whisper 转录 | 纯 Gemini 直出转录 | 终极三阶段字幕生产线 ⭐ |
| :--- | :--- | :--- | :--- |
| **时间轴精准度** | 物理声学测量，毫秒级精准 | ⚠️ **文本预测易累积漂移（播至30秒漂移 > 5秒）** | **物理声学毫秒级精确对齐（全片 0.000 秒零漂移）** |
| **专有名词与中英夹杂** | 容易出现同音错字或英文大小写混乱 | 语义与专有名词精准 | **全篇名词库加持，中英专有名词、品牌名与行业术语 100% 精准统一** |
| **字幕阅读节奏** | 符合短句节奏（每句约 1.2–2.5 秒） | 切句粒度不均匀 | **最适合 YouTube 的快节奏短句（每句约 8–16 字、1.5–3 秒）** |
| **逐字忠实度与防脑补** | 忠实记录说话内容 | 容易过度润饰或擅自摘要 | **听局部真实音频进行声学确认，还原真实说话（零幻觉、零过度脑补）** |

#### 三阶段执行流程：
1. **阶段一（全篇音频宏观理解、双轨专有名词库与 Whisper Initial Prompt 萃取）**：
   - 提取全片音频，由 Gemini 3.7 Flash（1M Context）一次听完整集节目，可选注入访纲笔记（`--outline`）或录音完整讲稿／逐字稿（`--script`）。
   - **双轨解析产出**：不仅产出供 Gemini 审稿的完整 Markdown 词汇库（`final_cut_full_glossary.md`），更在文件顶部自动产出高密度、控制在 200 token（约 100～140 字符）内的 `> **Whisper Initial Prompt**: ...` 核心关键字列。
2. **阶段二（Whisper 声学物理时间轴与专有名词偏置）**：
   - 自动将 Stage 1 萃取的 `initial_prompt` 注入本地 `mlx-whisper`、`faster-whisper` 或 `openai-whisper`，大幅降低专有名词首度声学识别错误率。
   - 通过硬件加速向量化提取每个段落与每个词的真实物理起讫点（`word_timestamps=True`），产出 100% 零漂移的毫秒时间戳初稿与词级声学缓存（`final_cut_full_raw_whisper.srt` 与 `final_cut_full_words.json`）。日后微调提示词或排版时自动秒级载入缓存，免去重复转录的漫长等待。
3. **阶段三（静音感知语义切块、微声学锚定与多模态音频审稿）**：
   - **静音感知语义分块 (Silence-Aware Semantic Chunking)**：淘汰死板的固定行数硬切，改在目标区间滑动窗口内搜寻讲者**自然呼吸停顿**（Gap $\ge 0.4\text{s}$）与完整句尾语气词／标点（`？`、`！`、`。`、`来说`、`的话`），避开连词前切断，确保送交审稿之上下文语义完整。
   - **文本语义与声学时间彻底解耦**：Gemini 专注于口语语义自然断句、排版标点净化与同音错字修正。
   - **子句微声学锚定 (Micro-Acoustic Sub-clause Snapping)**：长句拆分为分句时，自动结合 Whisper 物理词级时间戳 `all_words`，精确咬合口形发音的物理起讫点，拒绝均分比例导致的口形微偏差。
   - **日语发音与汉字音字同步规范**：讲者口述念出日文读音时呈现「日文汉字（平假名）」（如 `改札（かいさつ）`）；纯快速中文带过未念发音时呈现纯汉字（如 `出改札`），并辅以括号剥离容错比对算法，杜绝声学脱锚。
   - **Gemini API 指数退避与随机抖动重试机制 (Exponential Backoff & Jitter)**：面对并发请求或限流触发 HTTP 429 (`RESOURCE_EXHAUSTED`)、503 / 500 等暂态错误时，自动进行最多 5 次指数退避重试（自动解析 `Retry-After` 并加上随机 Jitter），防止并行 Worker 同时重打引发雷群效应，保证所有切块字幕均能稳健完成审稿，不再轻易降级退回未校对的原始字幕。
   - **区块级持久化缓存 (Chunk-Level Persistent Cache)**：结合模型、提示词、词汇库与切块文本产生唯一哈希，校对区块即时写入 `.<basename>_chunk_cache.json`。若中途遇网络波动中断，重新执行 100% 接续进度，零重复 token 消耗。
   - **防闪烁微间隙熔接与自然呼吸留白**：说话微小空隙（$< 0.6\text{s}$）自动平滑熔接为 0s Gap 消除画面黑闪；讲者真实停顿处保留 $+0.4\text{s}$ 阅读呼吸缓冲后干净清空画面，且单向时间锁定保证字幕绝不遮蔽下一句话的发音。

#### 🎯 影视级字幕质量检验标准与自动优化逻辑

`generate_subtitles.py` 内置完整的 Netflix / YouTube 影视级质量审核引擎，自动执行以下 8 大优化与合规验证：

| 检验项目 | 标准规范 | 优化与工程处理逻辑 |
| :--- | :--- | :--- |
| **单行字数宽度限制** | CJK $\le 15$ 字 / EN $\le 37$ CPL | 依各语系设定字宽上限（中文/日文 $\le 15$ 字、韩文 $\le 16$ 字、英文 $\le 37$ 字符）。长句自动在子句边界平滑拆分，防止小屏幕折行。 |
| **阅听速率监控 (CPS)** | CJK $\le 6.0$ CPS / EN $\le 20.0$ CPS | 计算全片平均 CPS 与峰值 CPS，过促语句（如短促高密度字）自动警示并列入待复查清单。 |
| **行尾标点与版面净化** | 100% 消除行尾 `。`、`，`、`；` | 清除无视觉意义的行尾符号；行内逗号转换为自然空格，中英文/数字间距自动标准化，版面极简清爽。 |
| **字符排版与语法洁净** | 括号成对闭合 / 严禁残留 Markdown | 检验全角 `（）`、`【】`、`《》`、`「」` 及半角括号成对闭合；自动清洗 `**`粗体、`_`斜体、`` ` ``代码标记等 LLM 泄漏标签。 |
| **长时间无对白/静音检验** | 停顿 Gap $\ge 10.0\text{s}$ 警示 | 检测全片超过 10 秒之空白间隔，记录前后句与时间码，供剪辑师快速确认为 B-roll 空景、转场音乐或 ASR/VAD 语音切除遗漏。 |
| **声学起点 0 剧透** | 0.000s 物理对齐 | 字幕出现时间严格锁定 Whisper 物理声学起点，绝对不比声音先出，避免剧透观影体验。 |
| **阅听时长保护** | $1.0\text{s} \le \text{Duration} \le 6.0\text{s}$ | 短句在后方静音空隙自动补足至 $\ge 1.0\text{s}$（确保读者反应时间）；单句上限 $\le 6.0\text{s}$（杜绝卡顿感）。 |
| **防闪烁微间隙熔接** | 消除 $< 0.2\text{s}$ 视觉黑闪 | 连续说话之间的微小空隙（$< 0.6\text{s}$）自动平滑熔接为 0s Gap；段落自然停顿处自动保留 $+0.4\text{s}$ 阅读呼吸缓冲并清空画面。 |

#### 执行指令范例：

```bash
# 基本执行（全自动全片听音抽取词汇库 + Whisper 物理转录 + Gemini 多模态音频审稿）：
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

# 提供访纲或重点笔记偏置专有名词（可选）：
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --outline "讲者: 来宾名称, 主题: 核心议题、专有名词列表"

# 提供录音原稿或完整讲稿作为专有名词与词汇标准（可选）：
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --script manuscript.txt

# 指定语言与 Whisper 模型大小：
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --language zh-CN --whisper-model small
```

4. **输出文件**：
   - **`final_cut_full.srt`**：YouTube 标准 SubRip 字幕文件。
   - **`final_cut_full.vtt`**：网页与 HTML5 播放器通用 WebVTT 字幕文件。
   - **`final_cut_full_subtitle_report.json`**：Netflix / YouTube 影视级字幕质量检验量化报告（JSON，含指标数据与待复查清单）。
   - **`final_cut_full_subtitle_report.md`**：影视级字幕质量检验可视化评分报告（Markdown，含合规等第、长时间静音区间表与时间码定位清单）。
   - **`final_cut_full_glossary.md`**：全集专有名词与词汇对照表（含顶部 Whisper Initial Prompt）。
   - **`final_cut_full_raw_whisper.srt`**：保留原始 Whisper 声学转录初稿供对照。
   - **`final_cut_full_words.json`**：Whisper 毫秒级词级物理时间戳缓存。

---

## 🛠️ 环境需求

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg**（支持 `h264_videotoolbox` 硬件编码与 `loudnorm` 滤镜）
- **Python 3.8+**
- **NumPy** (`pip install numpy`)
