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
  1. **全編音声マクロ理解、2系統用語集＆Whisper Initial Prompt 抽出**：Gemini 3.7 Flash（1M Context）で全編音声を聴取（インタビュー構成案 `--outline` や収録台本／原稿 `--script` の注入に対応）。Gemini 校正用の Markdown 用語集（`final_cut_full_glossary.md`）に加え、ファイル先頭に 200 token（約 100〜140 文字）以内の高密度キーワード列（`> **Whisper Initial Prompt**: ...`）を自動生成。
  2. **Whisper 物理音響タイムコード＆用語バイアス注入**：Stage 1 の `initial_prompt` をローカル Whisper（`mlx-whisper` / `faster-whisper`）に注入して専門用語の初回認識率を大幅向上。各単語の物理音声波形を測定（`word_timestamps=True`）し、ズレ累積 0.000 秒の基準タイムラインと単語キャッシュ（`final_cut_full_raw_whisper.srt` & `final_cut_full_words.json`）を生成（再実行時は秒単位でロード可能）。
  3. **無音感知セマンティック分割、マイクロ音響スナップ＆マルチモーダル校正**：
     - **無音感知セマンティック分割 (Silence-Aware Semantic Chunking)**：固定行数での機械的切断を廃止し、自然な呼吸ポーズ（Gap $\ge 0.4\text{s}$）や文末助詞・句読点で安全に分割。
     - **マイクロ音響スナップ (Micro-Acoustic Sub-clause Snapping)**：長文分割時、Whisper 単語物理タイムスタンプ（`all_words`）に吸着させ、比例配分による口元の微細なズレを排除。
     - **日本語漢字・読み仮名同期規則**：発言者が日本語読みを口述した場合は「漢字（ひらがな）」（例：`改札（かいさつ）`）、中国語会話中で触れたのみの場合は純粋な漢字（例：`出改札`）として処理し、括弧除去フォールバック照合で音響脱落を防止。
     - **チャンク単位の永続キャッシュ (Chunk-Level Persistent Cache)**：モデル・プロンプト・用語集・テキストから一意のハッシュを生成し、`.<basename>_chunk_cache.json` に即時保存。中断時もトークン消費ゼロで 100% 再開可能。
     - **フリッカー防止微小ギャップ結合**：微小な空隙（$< 0.6\text{s}$）を 0s に平滑化、真のポーズ時は $+0.4\text{s}$ の呼吸余白を付与して画面をクリーンにクリア。
- **🎯 Netflix / YouTube 配信標準字幕品質監査エンジン（8大監査項目）**：
  - **1行文字数・表示幅制限**：日本語/中国語 $\le 15$ 字、韓国語 $\le 16$ 字、英語 $\le 37$ CPL（長文は文節で自動分割）。
  - **読取速度監視 (CPS)**：日本語 $\le 6.0$ CPS、英語 $\le 20.0$ CPS。全編の平均 CPS とピーク CPS を算出し、Netflix 基準超過を警告リストへ登録。
  - **行末記号の完全除去**：行末の「。」「、」「；」を 100% 除去し、極めてクリーンなレイアウトを実現。
  - **タイポグラフィ・書式クレンジング**：全角 `（）`、`【】`、`《》`、`「」` および半角括弧の整合性検証、漏洩した Markdown タグ（`**`、`_`、`` ` ``）の自動消去。
  - **長時間無音・無対話区間検出**：10 秒以上の無音区間（Gap $\ge 10.0\text{s}$）を検出し、B-roll、BGM、または ASR 脱落の確認用に前後のテキストとタイムコードを記録。
  - **音声開始 0 秒完全同期**：Whisper 物理音響開始点に厳格固定（0.000s）し、字幕のネタバレを防止。
  - **閲覧時間保護**：$1.0\text{s} \le \text{Duration} \le 6.0\text{s}$（短いフレーズは空白時間を活用して自動補正）。
  - **フリッカー防止微小ギャップ結合**：$< 0.2\text{s}$ の微小ギャップを 0s に平滑化、$+0.4\text{s}$ の呼吸余白を確保。
- **実行コマンド例**：
  ```bash
  # 基本実行（用語集自動抽出＋Whisper転記＋Geminiマルチモーダル校正）：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # 台本／収録原稿を渡して用語と文脈を最適化：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --script manuscript.txt
  ```
- **出力**：
  - `final_cut_full.srt`：YouTube 標準 SubRip 字幕ファイル。
  - `final_cut_full.vtt`：Web プレイヤー用 WebVTT 字幕ファイル。
  - `final_cut_full_subtitle_report.json`：品質監査レポート（JSON、詳細数値と要確認リスト）。
  - `final_cut_full_subtitle_report.md`：品質監査評価カード（Markdown、適合グレードと無音区間表）。
  - `final_cut_full_glossary.md`：全編用語集（先頭に Whisper Initial Prompt 記載）。
  - `final_cut_full_raw_whisper.srt`：Whisper 転記初稿。
  - `final_cut_full_words.json`：Whisper 単語物理タイムスタンプキャッシュ。

---

## 🛠️ 必要環境

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg**（`h264_videotoolbox` ハードウェアエンコードおよび `loudnorm` 対応）
- **Python 3.8+**
- **NumPy** (`pip install numpy`)
