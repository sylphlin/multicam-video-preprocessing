# マルチカメラ動画インテリジェント前処理＆AI編集スイート (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity ネイティブスキル＆ワークフロー**  
> 本ツールキットは、**Google Antigravity Agent（Gemini 3.7 Flash 1M マルチモーダル長コンテキスト）** とプロフェッショナル向けノンリニア編集ソフト（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）のために設計されたマルチカメラ（2〜6台）スマート前処理パイプラインです。

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
- ローカル Whisper のミリ秒単位アライメント ＋ Gemini 1M コンテキストによる固有名詞・同音異義語校正により、高精度な `.srt` / `.vtt` を生成。

---

## 🚀 クイックスタートガイド

### プラン A：プロフェッショナルNLE編集ワークフロー（XMLエクスポート ⭐ 推奨）

```bash
# 1. マルチカメラ前処理（音声同期 + EBU R128音量正規化 + 30-40分チャプター分割 + マスター書き出し + グリッド合成）
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --split-min-dur 30 --split-max-dur 40 \
  --normalize --merge \
  -o ./output/

# 2. Gemini 3.7 Flash AIマルチモーダル粗編集決定（各Partごとに実行）
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3A. FCP7 XMLタイムラインのエクスポート
python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml
```

#### 🎬 DaVinci Resolve へのインポート手順：
1. DaVinci Resolveを開き、新規プロジェクトを作成します。
2. `./output/CAM1_synced.mp4` と `./output/CAM2_synced.mp4` を**メディアプール**にドラッグ＆ドロップします。
3. **ファイル $\\rightarrow$ 読み込み $\\rightarrow$ タイムライン...** を選択し、`final_cut_full.xml` を選択します。
4. 全カットポイント、メイン音声トラック、カラーMarkerマーカーが一瞬で読み込まれます！
