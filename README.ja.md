# マルチカメラ動画インテリジェント前処理＆AI編集スイート (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity ネイティブスキル＆ワークフロー**  
> 本ツールキットは、**Google Antigravity Agent（Gemini 3.7 Flash 1M マルチモーダル長コンテキスト）** とプロフェッショナル向けノンリニア編集ソフト（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）のために設計されたマルチカメラ（2〜6台）スマート前処理パイプラインです。

---

本プロジェクトは、Antigravity Agent の対話インターフェースを通じて自然言語で指示を出すだけで、マルチカメラ動画の同期、音量正規化、AI粗編集、XMLタイムライン書き出し、YouTube字幕作成までを自動実行します。

---

## 📦 Antigravity インストール＆ディレクトリ構造

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 ディレクトリ構造
```text
multicam-video-preprocessing/
├── GEMINI.md                          # Antigravity ワークスペース常駐ルール
├── .agent/
│   ├── rules/
│   │   └── multicam_rules.md          # 常駐ポリシー＆制約
│   └── workflows/
│       └── multicam_workflow.md       # 公式4段階実行ワークフロー (Stage-Gated Runbook)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity スキル機能定義
├── assets/                            # プロンプトテンプレート資産
│   ├── edl_interview_template.md      # Gemini 粗編集プロンプト
│   └── subtitle_proofread_template.md # YouTube 字幕校正プロンプト
├── scripts/                           # コア実行ツールセット
│   ├── multicam_pipeline.py           # Step 1: 音声同期・音量正規化・チャプター分割・グリッド合成
│   ├── generate_edl.py                # Step 2: Gemini 3.7 Flash マルチモーダル粗編集決定
│   ├── export_fcp7_xml.py             # Step 3A: FCP7 XMLタイムラインエクスポート (推奨)
│   ├── edl_to_video.py                # Step 3B: 動画直接レンダリング (プレビュー)
│   ├── concat_videos.py               # Step 3B: チャプター無劣化結合
│   ├── generate_subtitles.py          # Step 4: YouTube字幕生成 (Whisper + Gemini)
│   └── modules/                       # 音響＆映像コアアルゴリズムモジュール
└── README.ja.md
```

---

## 💬 利用シナリオとプロンプト例 (User Scenarios & Prompt Examples)

### シナリオ 1：編集用 XML タイムラインのエクスポート（DaVinci Resolve / Premiere Pro ⭐ 推奨）
- **プロンプト例**：
  > 「*2台のインタビュー動画 `CAM1.mp4` と `CAM2.mp4` があります。音声同期と音量正規化を行い、DaVinci Resolve で開ける XML タイムラインを書き出してください。*」
- **成果物**：
  1. `final_cut_full.xml`（全カットポイントとAI判定理由Marker付き統合タイムライン）
  2. `CAM1_synced.mp4`, `CAM2_synced.mp4`（音量正規化済み同期マスター動画）
- **DaVinci Resolve への読み込み手順**：
  1. DaVinci Resolveを開き、新規プロジェクトを作成。
  2. `./output/CAM1_synced.mp4` と `./output/CAM2_synced.mp4` を**メディアプール**にドラッグ＆ドロップ。
  3. **ファイル $\\rightarrow$ 読み込み $\\rightarrow$ タイムライン...** を選択し、`final_cut_full.xml` を選択。

---

### シナリオ 2：完成動画と YouTube 字幕の直接書き出し（プレビュー 🎬）
- **プロンプト例**：
  > 「*マルチカメラ素材を粗編集して、プレビュー用のMP4完成動画と校正済みYouTube字幕を出力してください。*」

---

### シナリオ 3：チャプター分割時間の間隔指定（カスタム分割 ⏱️）
- **プロンプト例**：
  > 「*チャプターを約10分前後の自然なポーズで分割して処理してください。*」
- **Agent の動作**：
  - 設定ファイルを変更することなく、自動的に分割パラメータを調整して実行します。

---

## 🔍 各ステップの処理詳細 (Detailed Pipeline Steps)

### ステップ 1：マルチカメラ物理前処理 (`multicam_pipeline.py`)
1. **8kHz FFT 音声時間同期**：音声を8kHzにダウンサンプリングして1D FFT相互相関関数を高速計算し、各カメラの開始録画ズレ $\\Delta t$ をミリ秒単位で正確に補正。
2. **EBU R128 (-14 LUFS) 音量正規化**：YouTube推奨基準である -14 LUFS（True Peak -1.5 dBTP）に合わせ、2-Pass loudnorm フィルターで音量を均一化。
3. **同期マスター動画の書き出し (`*_synced.mp4`)**：XMLタイムラインが直接参照する同期済み・音量均一化マスター動画を出力。
4. **30〜40分 自然な無音ポーズでのチャプター分割**：音声RMSエネルギーを走査し、文の途中で切れないよう自然な呼吸・無音位置で30〜40分ごとに分割（1M Token コンテキストに最適化）。
5. **2〜6台 マルチカメラコンパクトグリッド合成**：最大1080p以下、各画角480p以上のグリッド動画を合成し、AIトークン消費を50%〜83%削減。

### ステップ 2：Gemini AI マルチモーダル粗編集決定 (`generate_edl.py`)
- 発言者の音声を主導としてカメラアングルを決定し、適度なリスナーのリアクションカットを挿入、単一カット2.5秒以上のジャンプカット防止を適用して `edl_part*.csv` を出力。

### ステップ 3A：FCP7 XML タイムラインエクスポート (`export_fcp7_xml.py`)
- 全チャプターの時間軸を統合し、DaVinci Resolve / Premiere Pro / Final Cut Pro に直接読み込める `final_cut_full.xml` を生成。

### ステップ 4：YouTube 字幕生成 (`generate_subtitles.py`)
- **3段階ゴールデン字幕生成パイプライン（Three-Stage Pipeline）**：
  1. **全編音声グローバル用語集抽出**：Gemini 1M Context で全編音声を聴取し、固有名詞・英語名・専門用語集（`final_cut_full_glossary.md`）を自動生成。
  2. **Whisper 音響ミリ秒物理タイムコード**：ローカル Whisper で物理音声波形を測定し、ズレ累積 0.000 秒の基準タイムラインを生成。
  3. **マルチモーダル意味段落自然改行・リズム浄化・高精度校正**：多言語テンプレート（`zh-TW`、`en`、`ja`、`zh-CN`、`ko`）に対応し、国際標準に準拠した自然な改行（日本語/中国語 $\le 14\sim 15$ 字、韓国語 $\le 16$ 字、英語 $\le 37$ 文字）と閲覧時間自動補正（各行 $\ge 1.0\text{s}$、ポーズ余白 $+0.4\text{s}$、フリッカー除去）を行い、誤字・専門用語を高精度に校正。
- **出力**：`final_cut_full.srt`、`final_cut_full.vtt`、`final_cut_full_subtitle_report.json`（品質監査JSON）、`final_cut_full_subtitle_report.md`（評価Markdown）、`final_cut_full_glossary.md`。

---

## 🛠️ 必要環境

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg**（`h264_videotoolbox` ハードウェアエンコードおよび `loudnorm` 対応）
- **Python 3.8+**
- **NumPy** (`pip install numpy`)
