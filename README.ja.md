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
