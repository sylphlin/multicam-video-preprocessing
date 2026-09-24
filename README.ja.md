# Multi-Camera Video Pipeline & AI Editing Suite

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **Google Antigravity ネイティブプラグイン＆ワークフロースイート**  
> 本ツールキットは、**Google Antigravity**（**Vertex AI Gemini 3.8 Flash** 搭載）およびプロ向け NLE（**DaVinci Resolve**、**Adobe Premiere Pro**、**Final Cut Pro**）向けの 4 ステージ・マルチカメラ前処理＆AI ラフカットスイートです。

---

**Multi-Camera Video Pipeline & AI Editing Suite** は、2〜6 台のカメラ映像の音響同期、ラウドネス正規化、AI ラフカットタイムライン生成、および YouTube 字幕生成を実行します。

---

## インストールと Google Cloud セットアップ (`setup.sh`)

本プロジェクトは [Agent Plugins 1.0](https://agent-plugins.org/) に準拠し、**Google Cloud Vertex AI (ADC)** と **Cloud Storage (GCS)** 上で動作します（API キー管理不要）。

```bash
# 1. グローバル Antigravity Plugin としてクローン
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/plugins/multicam-video-preprocessing

# 2. 依存パッケージのインストールと ADC 認証
brew install ffmpeg
pip install numpy google-genai google-cloud-storage mlx-whisper requests
gcloud auth application-default login

# 3. setup.sh を実行して GCS バケット、2 階層ライフサイクル（raw: 2 日、成果物: 15 日）、IAM、.env を構成
chmod +x setup.sh
./setup.sh --project YOUR_GCP_PROJECT_ID
```

### ディレクトリ構造（Agent Plugins 1.0 準拠）
- **SSOT 実体ディレクトリ**：`skills/multicam-video-preprocessing/`（`SKILL.md`、`scripts/`、`assets/` を格納）を単一の信頼できる情報源とし、ルートの `scripts` と `assets` は POSIX シンボリックリンクとして構成されています。
- **2 層 `AGENTS.md` 構成**：ルートの `AGENTS.md` は開発・エンジニアリング規約（Part I & Part II）を定義し、`rules/AGENTS.md` はプラグインに同梱される AI クライアント実行時ルール（`<PLUGIN_ROOT>` からの直接 CLI 実行、読み取り専用、Fail-Fast）を定義します。

---

## 4 ステージ実行ワークフローと CLI コマンド

### Stage 1：マルチカメラ同期＆前処理 (`multicam_pipeline.py`)
1. **MFCC 音響アライメント＆サブフレーム微調整（`<0.125 ms`）**：3 段階スキャンと `--strict-sync` による低信頼度ゲートを備えています。
2. **EBU R128 (`-14 LUFS`) 2 パス線形ラウドネス正規化**：ダイナミックレンジを損なわずに `-14.0 LUFS` に統一します。
3. **フレーム精度同期マスター出力 (`CAM*_synced.mp4`)**：ハードウェアエンコードによりキーフレームずれを排除します。
4. **ゼロ分割グリッド合成 (`multicam_merged_full.mp4`)**：2〜6 カメラを単一キャンバス（$\le 1920 \times 1080$）に合成します。

```bash
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 CAM3.mp4 \
  --normalize --merge -o output/
```

### Stage 2：Gemini 3.8 Flash Agentic Video ラフカット (`generate_edl.py`)
**Vertex AI Gemini 3.8 Flash**（`processing="agentic"`）で `edl_full.csv` を生成し、8 項目の決定論的 EDL 検証（`6 ERROR + 2 WARN`）を実行します：

```bash
python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --strict-edl --lang ja
```

### Stage 3A：FCP7 XML タイムライン出力 (`export_fcp7_xml.py`)
```bash
python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml --fps 29.97 --drop-frame
```

### Stage 3B：シングルパス動画レンダリング (`edl_to_video.py`)
```bash
python3 scripts/edl_to_video.py --edl output/edl_full.csv -o output/final_cut_full.mp4 --strict-edl
```

### Stage 4：3 ステージ・ゴールデン字幕パイプライン (`generate_subtitles.py`)
**Vertex AI 1M 用語集**、**Whisper 単語タイムスタンプ**、**Gemini マルチモーダル音声校正＋8 次元品質監査**を実行します：

```bash
python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --language ja
```

---

## Google Drive 連携と GCS 2 階層ライフサイクルポリシー

| GCS パス接頭辞 (`matchesPrefix`) | 保存対象 | 保持期間 (`age`) | クリーンアップ動作 |
| :--- | :--- | :--- | :--- |
| **`raw/audio_chunks/`** | Stage 4.3 音声チャンク | **推論直後に即時削除** | 各チャンク完了後に Python `finally` ブロックで即時削除します。 |
| **`raw/`** | 一時グリッド動画およびエピソード音声 | **2 日間 (`age: 2`)** | SHA-256 キャッシュ再利用のため 2 日間保持し、自動削除します。 |
| **`output/`**、**`deliverables/`**、**`multicam_assets/`** | XML/CSV タイムライン、SRT/VTT 字幕、レポート | **15 日間 (`age: 15`)** | チーム確認用に 15 日間保持した後、自動削除します。 |

---

## ライセンス (License)

本プロジェクトは [MIT License](LICENSE) の下で提供されています。
