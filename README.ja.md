# マルチカメラ映像智慧処理＆AI編集スイート (Multicam Video Pipeline & AI Editing Suite)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!NOTE]
> **プラットフォーム対応と環境について**  
> - **検証環境**：本ツールキットは **Google Antigravity 2.0** および **Gemini 3.7 Flash (Thinking: Medium)** 環境で設計・実証テストされています。  
> - **クロスプラットフォーム対応**：**[Agent Plugins 1.0 仕様](https://agent-plugins.org/specification)** に準拠してパッケージ化されており、対応クライアント（**OpenAI Codex デスクトップ版**など）でも利用可能です。全プラットフォームでの検証は進行中であり、フィードバックを歓迎します。  
> - **コンテキストサイズと分割設定**：他のマルチモーダルモデルを使用する際は、**コンテキストウィンドウ（Context Window）サイズ**を確認し、Step 1 のチャプター分割パラメータ（`--split-min-dur` / `--split-max-dur`、デフォルト: 30〜40分）を適宜調整してください。

---

長文コンテキストマルチモーダルAIモデル（Gemini 3.7 Flash 1Mトークンコンテキスト）およびプロ向けNLE（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）に最適化されたマルチカメラ（2〜6台）映像処理パイプライン＆AI粗編集ツールキットです。

---

## 📦 インストール＆導入ガイド (Installation & Setup)

Antigravity および Agent Plugins 1.0 標準仕様に準拠しており、スキルディレクトリにクローンして利用できます：

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 ディレクトリ構成
```text
multicam-video-preprocessing/
├── plugin.json                    # Agent Plugins 1.0 マニフェスト (Codex 等対応)
├── SKILL.md                       # Antigravity スキル定義＆分岐ルール
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md               # Agent Plugins 1.0 スキル定義
├── assets/                        # プロンプト資産 (Prompt Assets)
│   ├── edl_interview_template.md  # 2台カメラインタビュー用プロンプトテンプレート
│   └── subtitle_proofread_template.md # YouTube 字幕校正テンプレート
├── scripts/                       # 実行スクリプト＆処理モジュール
│   ├── multicam_pipeline.py       # Step 1: 音声同期、EBU R128、チャプター分割、グリッド合成
│   ├── generate_edl.py            # Step 2: マルチモーダル AI 粗編集決定生成
│   ├── export_fcp7_xml.py         # Step 3A: FCP7 XML タイムライン出力 (主要)
│   ├── edl_to_video.py            # Step 3B: 直接動画レンダリング (次要)
│   ├── concat_videos.py           # Step 3B: 全編無損失ストリーム結合 (次要)
│   ├── generate_subtitles.py      # Step 4: YouTube 字幕生成 (Whisper+Gemini)
│   └── modules/                   # 內部音声・映像アルゴリズムモジュール
└── README.md
```

---

## 🌟 エンドツーエンド全ワークフロー

```mermaid
flowchart TD
    A["未処理マルチカメラ素材 (2–6 CAMs)"] --> B["手順 1：マルチカメラ同期＆グリッド前処理<br/>(multicam_pipeline.py)"]
    
    B --> C["【全編同期マスター動画】<br/>• CAM1_synced.mp4<br/>• CAM2_synced.mp4"]
    B --> D["【AI 分析用グリッド動画】<br/>• multicam_merged_part*.mp4"]
    
    D --> E["手順 2：AI マルチモーダル粗編集決定<br/>(generate_edl.py / Antigravity)"]
    E --> F["【EDL 編集決定リスト】<br/>• edl_part*.csv"]
    
    C --> G{"出力形式の選択"}
    F --> G
    
    G -->|"主要：プロ向け NLE 編集 (90%)"| H["手順 3A：FCP7 XML タイムライン出力<br/>(export_fcp7_xml.py)<br/>DaVinci Resolve / Premiere Pro にインポート"]
    G -->|"次要：直接動画出力 (10%)"| I["手順 3B：MP4 完成動画の直接レンダリング＆結合<br/>(edl_to_video.py + concat_videos.py)<br/>final_cut_full.mp4 を出力"]
    
    I --> J["手順 4：YouTube 字幕生成<br/>(generate_subtitles.py)<br/>Whisper 音声認識 ＋ Gemini 意味校正<br/>final_cut_full.srt / .vtt を出力"]
```

---

## 💬 ユースケース＆プロンプト例

Antigravity 対話画面で自然言語で指示するだけで、Agent がバックエンドモジュールを自動実行します：

### ユースケース 1：編集用 XML の出力（プロ向け編集ワークフロー）
- **適用シーン**：粗編集結果を DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro に読み込み、カラーグレーディングや整音を行う場合。
- **対話プロンプト例**：
  > 「*`CAM1.mp4` と `CAM2.mp4` の2台のインタビュー素材があります。タイムライン同期と音量正規化を行い、インタビュー編集ルールを適用して DaVinci Resolve 用の XML タイムラインを出力してください。*」
- **納品成果物**：
  1. `final_cut_full.xml`（98以上のカット点とカラーマーカー付きタイムライン）
  2. `CAM1_synced.mp4`、`CAM2_synced.mp4`（音画同期および -14 LUFS 正規化済みマスター動画）
- **DaVinci Resolve インポート手順**：
  1. DaVinci Resolve で新規プロジェクトを作成。
  2. `CAM1_synced.mp4` と `CAM2_synced.mp4` を **メディアプール** にドラッグ。
  3. **ファイル $\rightarrow$ 読み込み $\rightarrow$ タイムライン...** (`Cmd + Shift + I`) を選択し、`final_cut_full.xml` を読み込みます。

---

### ユースケース 2：直接動画出力＆YouTube字幕（プレビュー＆配信ワークフロー）
- **適用シーン**：編集ソフトを開かずに、MP4 動画と YouTube 字幕を即座に作成して確認・配信したい場合。
- **対話プロンプト例**：
  > 「*この2台のマルチカメラ素材を粗編集して完全な MP4 動画を出力し、校正済みの YouTube 字幕も生成してください。*」
- **納品成果物**：
  1. `final_cut_full.mp4`（レンダリングおよび無損失結合された完成動画）
  2. `final_cut_full.srt` / `final_cut_full.vtt`（Whisper 音声同期＋Gemini 意味校正済み YouTube 標準字幕）

---

## 🔍 各ステップの処理詳細

### 手順 1：マルチカメラ同期＆グリッド前処理 (`multicam_pipeline.py`)
1. **8kHz FFT 音声タイムライン同期**：
   - 音声を 8kHz モノラルにダウンサンプリングし、相互相関アルゴリズムで物理的時間ズレ $\Delta t$（ミリ秒精度）を算出。
2. **EBU R128 (-14 LUFS) 全編音量ノーマライズ**：
   - 2パスのラウドネス解析とフィルタ処理により、全カメラ音声を -14.0 LUFS、11.0 LRA、-1.5 dBTP に統一。
3. **30〜40分 自然なポーズ検出チャプター分割 (Auto-Split)**：
   - 30〜40分の目標時間枠内で音声エネルギー最小値と自然な息継ぎを検出し、無損失で分割。
4. **全編同期マスター動画の出力 (`*_synced.mp4`)**：
   - $\Delta t$ に基づきトリミングした同期マスター動画を出力。
5. **マルチインワン画面合成 (2〜6台)**：
   - 画面サイズ $\le 1920 \times 1080$、各カメラ $\ge 640 \times 480$ のグリッド動画を合成し、**Token 消費を 50%〜83% 削減**。

---

### 手順 2：Gemini AI マルチモーダル粗編集決定 (`generate_edl.py`)
1. **プロンプト資産の読み込み**：
   - `assets/edl_interview_template.md` を読み込み。
2. **Phase 0：前後の不要部分トリミング (Pre/Post-roll Trimming)**：
   - 収録前の準備・カウントダウン（`Global_Start_Time`）および終了後の雑談・マイク音（`Global_End_Time`）を自動検出し除外。
3. **Phase 1–4：意味構造に基づくカット判断**：
   - **話者認識と追跡**：発話者の音声に合わせてカメラを切り替え。
   - **リアクションショット挿入**：短い相槌を無視し、大きなリアクション時に 2〜3 秒切り替え。
   - **カッティングテンポ維持**：1カットの長さを $\ge 2.5\text{s}$ に維持。
4. **標準フォーマット出力**：
   - CSV 決定表（`edl_part*.csv`）および Markdown 分析レポート（`edl_part*_report.md`）を出力。

---

### 手順 3A（主要）：FCP7 XML タイムライン出力 (`export_fcp7_xml.py`)
1. **複数チャプターのタイムスタンプ積算**：
   - Part 1、Part 2 のタイムスタンプを通算タイムラインに変換。
2. **1:1 タイムコード完全一致**：
   - 各カットの `start == in`、`end == out` を維持し、NLE 上でのトリミング調整（Slip/Slide）に対応。
3. **主音声トラックとルールマーカーの付与**：
   - 連続した CAM1 主音声トラックを構築し、編集根拠を示すカラーマーカーを配置。

---

### 手順 3B（次要）：直接レンダリング＆結合 (`edl_to_video.py` & `concat_videos.py`)
1. **ハードウェア加速によるチャプター出力**：
   - Apple Silicon (`h264_videotoolbox`) で各チャプター動画（`final_cut_part*.mp4`）を出力。
2. **無損失ストリーム結合**：
   - FFmpeg Concat Demuxer（`-c copy`）で全編 `final_cut_full.mp4` に結合。

---

### 手順 4：YouTube 字幕生成 (`generate_subtitles.py`)

**Whisper（音声認識とタイムスタンプ同期）** と **Gemini（文脈意味校正と専門用語修正）** を組み合わせた2段階方式を採用しています：

#### なぜ「Whisper + Gemini」を使用するのか

| 比較項目 | Whisper 単体 | Gemini 音声認識単体 | Whisper + Gemini |
| :--- | :--- | :--- | :--- |
| **タイムスタンプ精度** | ミリ秒単位の高精度 | 粒度が粗い（段落単位） | ミリ秒単位の高精度（Whisper タイムコードを継承） |
| **同音異義語・誤字修正** | 同音の誤変換が発生しやすい | 文脈理解に優れる | 同音異義語や専門用語を自動校正 |
| **字幕の表示テンポ** | 短文リズム（約 1.2–2.5秒） | 1文が長い（約 6–8秒） | YouTube に適した短文（1行 8–16文字程度） |
| **発言の忠実度** | 発言内容をそのまま記録 | 要約や意訳が発生しやすい | 発言を忠実に保ちつつ誤字のみ修正 |
| **処理コスト** | ローカル処理で高速 | 音声 Token を消費 | 音声はローカル処理、Gemini はテキスト校正のみ |

#### 処理フロー：
1. **音声抽出**：FFmpeg で完成動画から 16kHz モノラル WAV 音声を抽出。
2. **第1段階（Whisper 音声認識）**：ローカルの `faster-whisper` でミリ秒精度のタイムスタンプ付き基準 SRT を生成。
3. **第2段階（Gemini 意味校正）**：タイムスタンプと番号を固定したまま、同音誤字や英単語（`Kelly Tsai`、`YouTube`、`DaVinci Resolve`、`Buffet` など）を自動校正。
4. **出力ファイル**：
   - **`final_cut_full.srt`**：YouTube 標準 SubRip 字幕ファイル。
   - **`final_cut_full.vtt`**：WebVTT 字幕ファイル。
   - **`final_cut_full_raw_whisper.srt`**：比較用 Whisper 原本ファイル。
