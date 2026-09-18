# マルチカメラ動画インテリジェント前処理＆AI編集スイート (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity ネイティブスキル＆ワークフロー**  
> 本ツールキットは、**Google Antigravity Agent（Gemini 3.8 Flash 1M マルチモーダル長コンテキスト）** とプロフェッショナル向けノンリニア編集ソフト（DaVinci Resolve、Adobe Premiere Pro、Final Cut Pro）のために設計されたマルチカメラ（2〜6台）スマート前処理パイプラインです。

---

本プロジェクトは、Antigravity Agent の対話インターフェースを通じて自然言語で指示を出すだけで、マルチカメラ動画の同期、音量正規化、AI粗編集、XMLタイムライン書き出し、YouTube字幕作成までを自動実行します。

---

## 📦 Antigravity インストール＆ワンクリック展開 (`setup.sh`)

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
cd ~/.gemini/config/skills/multicam-video-preprocessing
./setup.sh --project YOUR_GCP_PROJECT_ID
```

### 📁 ディレクトリ構造
```text
multicam-video-preprocessing/
├── AGENTS.md                          # ワークスペース常駐＆開発エンジニアリング規約 (Workspace & Dev Rules)
├── plugin.json                        # Agent Plugins 1.0 マニフェスト
├── setup.sh                           # ワンクリック GCP (ADC/Vertex AI/GCS) 環境構築スクリプト
├── rules/
│   └── AGENTS.md                      # プラグイン常駐不変ルール (Operational Invariants for AI Clients)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity スキル機能定義＆4段階実行 Runbook
├── assets/                            # プロンプトテンプレート資産
│   ├── edl_interview_template.md      # Gemini 粗編集プロンプト
│   └── subtitle_proofread_template.md # YouTube 字幕校正プロンプト
├── scripts/                           # コア実行ツールセット
│   ├── multicam_pipeline.py           # Step 1: 音声同期・音量正規化・マスター出力・全編グリッド合成
│   ├── generate_edl.py                # Step 2: Gemini 3.8 Flash Agentic Video 分割不要粗編集決定
│   ├── export_fcp7_xml.py             # Step 3A: FCP7 XMLタイムラインエクスポート (推奨)
│   ├── edl_to_video.py                # Step 3B: ワンパス直接動画レンダリング (プレビュー)
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

---

## 🔍 各ステップの処理詳細 (Detailed Pipeline Steps)

### ステップ 1：マルチカメラ物理前処理 (`multicam_pipeline.py`)
1. **MFCC 音響特徴相互相関＆サブフレーム物理音響補正 (<0.125ms 精度)**：
   - **MFCC 音響特徴相互相関の採用理由**：人間の音声と過渡音響特性を最も的確に捉えるメル周波数ケプストラム係数（MFCC）を採用。従来の生波形相関に比べ FFT メモリ使用量を **97.7% 削減**（1時間音声でわずか約 12.5MB）、1時間の素材を 0.3 秒以内に高速同期し、マイク周波数特性の違いや背景ノイズに極めて強い耐性を実現。
   - **3段階フォールバックラダー (3-Tier Fallback Ladder)**：
     1. *高速 120s MFCC 探査*：先頭 120 秒の音声を抽出し、BBC 基準スコア $Z \ge 12.0$（高信頼度）の場合は 0.4 秒以内に同期完了。
     2. *全編 MFCC スキャン*：初期スコア $< 12.0$ または `--full-scan` 指定時に全編スキャンを実行。
     3. *生波形 FFT フォールバック*：全編 MFCC スコアが低い場合（$Z < 7.0$）、従来のハイパス生波形 1D FFT 相互相関へ自動フォールバック。
   - **サブフレーム物理音響微調整 (<0.125ms)**：両カメラの重複領域内で最大エネルギーの 5 秒間を特定し、$\pm 32\text{ms}$ の探索範囲内で時間領域相互相関を実行。精度を単一音声サンプル単位（**8kHz で 0.125ms の物理音響精度**）まで引き上げます。
   - **BBC 放送基準信頼度スコア＆低スコア即時警告**：相関ピークとノイズフロアの標準偏差比により評価：
     - $Z \ge 12.0$ 高信頼度：`✓ Aligned` と表示。
     - $7.0 \le Z < 12.0$ 中信頼度：`ℹ Aligned (marginal)` と表示。
     - $Z < 7.0$ 低信頼度：`⚠️ LOW CONFIDENCE` と表示し、即時に stderr 警告と原因診断ヒントを出力。
   - **Summary Gate 警告ボックス＆ `--strict-sync` 安全停止**：Step 1 終了時に低信頼度カメラが存在する場合、警告ボックスを端末に表示。`--strict-sync` 指定時は終了コード 1 で即時停止し、自動処理でのズレ事故を防止。
   - **コンテナ実再生時間の自動検出（`--sample-dur` 早期打ち切りバグの修正）**：`ffprobe` により動画コンテナの真の長さを取得し、テストモード時でもマスター出力と重複区間が全編維持されるよう保証。
2. **EBU R128 (-14 LUFS) 2-Pass リニア音量正規化**：Pass 1 で null sink を用いて高速音響測定（`I`, `LRA`, `TP`, `target_offset`）。Pass 2 で `linear=true` を適用し、ダイナミックポンピング（音量息継ぎ感）を完全根絶して -14.0 LUFS に 100% 精密固定。
3. **同期マスター動画のフレーム精度並列書き出し (`*_synced.mp4`)**：キーフレーム（I-frame）吸着によるミリ秒ズレや黒画面カクつきを防ぐため、デフォルトでフレーム精度のハードウェア再エンコード（`h264_videotoolbox` / `libx264 -crf 18`）を採用。高速粗編集用の `--stream-copy` もサポート。
4. **分割不要 全編マルチカメラコンパクトグリッド合成 (`multicam_merged_full.mp4`)**：最大1080p以下、各画角480p以上のグリッド動画を合成し、Agentic Video による全編直接理解を可能に。
- **実行コマンド例**：
  ```bash
  # 標準 4-in-1 前処理パイプライン（同期、音量正規化、マスター再エンコード、グリッド合成）：
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 \
    --targets CAM2.mp4 CAM3.mp4 \
    --normalize --merge -o output/

  # 高速同期テスト（先頭60秒のみ抽出して同期、コンテナ長は ffprobe で自動探測）：
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --sample-dur 60 --normalize --merge -o output/

  # 強制全編 MFCC スキャン（120s 快速ラダーをバイパス）：
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --full-scan --normalize --merge -o output/

  # 厳格同期検証モード（信頼度スコア 7.0 未満で即時エラー停止）：
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --strict-sync --normalize --merge -o output/

  # 高速ストリームコピーモード（-c copy、キーフレーム吸着）：
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --stream-copy --normalize --merge -o output/
  ```

### ステップ 2：Gemini 3.8 Flash Agentic Video 粗編集決定 (`generate_edl.py`)
1. **プロンプトテンプレートの読み込み**：`assets/edl_interview_template.md` による放送基準の厳格な編集ルールを適用。
2. **Phase 0：前後の無駄・現場カウントダウンの完全排除（ゼロトレランスと非対称セーフティマージン）**：
   - **現場カウントダウンの完全排除**：機材確認、雑談、カチンコ、現場のカウントダウン（「5, 4, 3, 2, 1」「3, 2, 1」など）を完全検知・除外。
   - **非対称セーフティマージン（`[Start, Start+2s]` 自己検証）**：`Global_Start_Time` は必ず最後のカウントダウン音が完全に終了した後に設定。カット開始後の最初の2秒間（`[Global_Start_Time, Global_Start_Time + 2.0s]`）で思考チェーンによる自己検証を行い、カウントダウンが残っている場合は開始点を後ろに移動させ、本編最初の単語に完璧にアライン。
   - **終了後未停止区間の除外**：インタビュー終了の挨拶を識別し、収録後の雑談やサムネイル撮影などの無駄な映像（`Global_End_Time`）を除外。
3. **次世代アーキテクチャ：Agentic Video Understanding（分割不要の全編編集）**：
   - Gemini 3.8 Flash の Agentic Video 機能（`processing="agentic"`）により、1時間以上のノーカットグリッド映像を直接評価。
   - 目的指向の動的スパースサンプリングにより、入力トークン消費を **99.7% 削減**（約 100 万トークンから約 3,000 トークンへ激減）。チャプター境界による発言分断を完全に解消。
4. **標準出力**：
   - 単一の標準 CSV 決定リスト（`edl_full.csv`）および Markdown 編集レポート（`edl_full_report.md`）を出力。
5. **EDL セマンティックバリデータ＆ `--strict-edl` 安全停止**：
   - **8 項目の整合性検査 (6 ERROR + 2 WARN)**：
     - `E_NO_ROWS` (ERROR)：EDL にカットデータ行が存在しない。
     - `E_PARSE_TIME` (ERROR)：タイムコード書式が解析不能（start / end 列の異常）。
     - `E_NEGATIVE_DURATION` (ERROR)：カット長が 0 秒以下（`end <= start`）。
     - `E_NON_MONOTONIC` (ERROR)：開始時間が前行より後退。
     - `E_OVERLAP` (ERROR)：隣接カット間で時間重複が発生。
     - `E_EMPTY_CAMERA` (ERROR)：カメラ名（camera）列が空。
     - `W_UNKNOWN_CAMERA` (WARN)：カメラ名が既知ホワイトリスト外（デフォルトは正規表現 `^CAM\d+$` または指定リスト）。
     - `W_GAP` (WARN)：隣接カット間の隙間が閾値超過（`--edl-max-gap-sec`、デフォルト `0.05` 秒）、タイムラインに黒フレームが発生。
   - **3 箇所の厳格な検証ポイント**：
     1. `generate_edl.py`：CSV 出力前に検証。**成否にかかわらず必ず CSV と分析レポートをディスクに書き出した上で終了判定を行う**ため、異常データも確実に確認・デバッグ可能。検証レポートは `edl_full_report.md` に追加され、その H2 見出しは `--lang` に応じてローカライズされます（`en` は `🔍 EDL Validation Result`、`zh-TW` は `🔍 EDL 驗證結果`）。
     2. `export_fcp7_xml.py`：EDL 読み込み時に検証。素材ディレクトリからカメラホワイトリストを自動推論。
     3. `edl_to_video.py`：動画レンダリング前に検証。素材ディレクトリまたはカメラマップから自動推論。
   - **安全停止ゲート (`--strict-edl`)**：デフォルトは警告のみで処理を継続。`--strict-edl` 指定時は `ERROR` 検出で終了コード 1 で即座にエラー停止し、不正な EDL の後続波及を防止。
   - **多言語レポート対応 (`--lang`)**：`en`（デフォルト）と `zh-TW` に標準対応。`zh-Hant`、`zh_TW` などの表記揺れは `zh-TW` へ自動正規化され、未対応言語はエラーを出さず静かに `en` へフォールバック。
   - **カスタマイズ引数**：`--edl-max-gap-sec`（デフォルト `0.05` 秒）；`--edl-known-cameras`（カンマ区切りホワイトリスト、例：`CAM1,CAM2`、未指定時は `^CAM\d+$`）。
6. **100% Google Cloud Vertex AI (ADC) + GCS スマートキャッシュ＆2段階自動ライフサイクル構成**：
   - **API キー管理ゼロのセキュア認証**：Google Cloud Vertex AI（`GOOGLE_CLOUD_LOCATION=global`）と Application Default Credentials（ADC）のみを使用し、AI Studio（`GEMINI_API_KEY`）や外部 File API へのアップロードを完全排除。
   - **GCS SHA-256 キャッシュ＆2段階自動削除（2日／15日）**：グリッド映像や音声は `gs://multicam-video-${PROJECT_ID}/raw/` にアップロードされ、ローカル SHA-256 キャッシュにより再アップロードを防止。一時ファイル（`raw/`）は **2日後**、成果物（`output/`、`deliverables/`、`multicam_assets/`）は **15日後** に GCS ライフサイクルルールで自動削除されます（`--cleanup-gcs` で推論完了直後に即時削除も可能）。
   - **実行コマンド例**：
     ```bash
     # 標準実行: Google Cloud Vertex AI (ADC + GCS キャッシュ):
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4

     # 推論完了後に GCS 上の一時動画を即時削除:
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --cleanup-gcs

     # 厳格 EDL 検証モード（ERROR 検出時に即時停止）:
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --strict-edl

     # 検証レポートの言語指定:
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --lang zh-TW

     # ギャップ許容閾値とカメラホワイトリストの個別設定:
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 \
       --strict-edl --lang zh-TW --edl-max-gap-sec 0.05 --edl-known-cameras CAM1,CAM2
     ```

### ステップ 3A：FCP7 XML タイムラインエクスポート (`export_fcp7_xml.py`)
- 全編同期マスター動画と `edl_full.csv` を直接リンクし、DaVinci Resolve / Premiere Pro / Final Cut Pro に直接読み込める `final_cut_full.xml` を生成。
- **NTSC 浮動小数点フレームレート (29.97 / 23.976 / 59.94) & Drop-Frame (`--drop-frame`) 完全対応**：
  - `--fps` 浮動小数点入力に対応し、FCP7 XML 仕様に準拠した整数 `<timebase>` と `<ntsc>TRUE</ntsc>` を自動出力。タイムコード計算は浮動小数点を用い、長尺動画でのフレーム累積ズレを完全解消。
  - `--drop-frame` により `<displayformat>DF</displayformat>` の出力に対応。
- **実行コマンド例**：
  ```bash
  # 標準 30 fps XML 書き出し：
  python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml

  # 厳格 EDL 検証とレポート言語指定（カメラホワイトリストは素材から自動推論）：
  python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml --strict-edl --lang zh-TW

  # 放送用 29.97 fps NTSC Drop-Frame XML 書き出し：
  python3 scripts/export_fcp7_xml.py -e output/edl_full.csv -o output/final_cut_full.xml --fps 29.97 --drop-frame
  ```

### ステップ 3B：ワンパス動画直接レンダリング (`edl_to_video.py`)
- 中間チャプター動画の書き出しや結合を介さず、Apple Silicon `h264_videotoolbox` を用いて同期マスターから直接 `final_cut_full.mp4` を一発レンダリング。
- **実行コマンド例**：
  ```bash
  # 標準ワンパス直接レンダリング：
  python3 scripts/edl_to_video.py --edl output/edl_full.csv -o output/final_cut_full.mp4

  # 厳格 EDL 検証とレポート言語指定：
  python3 scripts/edl_to_video.py --edl output/edl_full.csv -o output/final_cut_full.mp4 --strict-edl --lang zh-TW
  ```

### ステップ 4：YouTube 字幕生成 (`generate_subtitles.py`)
- **3段階ゴールデン字幕生成パイプライン（Three-Stage Pipeline）**：
  1. **全編音声マクロ理解、2系統用語集＆Whisper Initial Prompt 抽出**：Gemini 3.8 Flash（1M Context）で全編音声を聴取（インタビュー構成案 `--outline` や収録台本／原稿 `--script` の注入に対応）。Gemini 校正用の Markdown 用語集（`final_cut_full_glossary.md`）に加え、ファイル先頭に 200 token（約 100〜140 文字）以内の高密度キーワード列（`> **Whisper Initial Prompt**: ...`）を自動生成。
  2. **Whisper 物理音響タイムコード＆用語バイアス注入**：Stage 1 の `initial_prompt` をローカル Whisper（`mlx-whisper` / `faster-whisper`）に注入して専門用語の初回認識率を大幅向上。各単語の物理音声波形を測定（`word_timestamps=True`）し、ズレ累積 0.000 秒の基準タイムラインと単語キャッシュ（`final_cut_full_raw_whisper.srt` & `final_cut_full_words.json`）を生成（再実行時は秒単位でロード可能）。
  3. **無音感知セマンティック分割、マイクロ音響スナップ＆マルチモーダル校正**：
     - **無音感知セマンティック分割 (Silence-Aware Semantic Chunking)**：固定行数での機械的切断を廃止し、自然な呼吸ポーズ（Gap $\ge 0.4\text{s}$）や文末助詞・句読点で安全に分割。
     - **マイクロ音響スナップ (Micro-Acoustic Sub-clause Snapping)**：長文分割時、Whisper 単語物理タイムスタンプ（`all_words`）に吸着させ、比例配分による口元の微細なズレを排除。
     - **日本語漢字・読み仮名同期規則**：発言者が日本語読みを口述した場合は「漢字（ひらがな）」（例：`改札（かいさつ）`）、中国語会話中で触れたのみの場合は純粋な漢字（例：`出改札`）として処理し、括弧除去フォールバック照合で音響脱落を防止。
     - **Gemini API 指数バックオフ＆ジッター自動リトライ**：並行リクエストやレート制限による HTTP 429 (`RESOURCE_EXHAUSTED`)、503 / 500 エラー時に最大5回自動リトライ（`Retry-After` 自動解析とジッター付与）。ワーカーの同時再試行による競合を防ぎ、未校正字幕への安易なフォールバックを防止。
     - **チャンク単位の永続キャッシュ (Chunk-Level Persistent Cache)**：モデル・プロンプト・用語集・テキストから一意のハッシュを生成し、`.<basename>_chunk_cache.json` に即時保存。中断時もトークン消費ゼロで 100% 再開可能。
     - **フリッカー防止微小ギャップ結合**：微小な空隙（$< 0.6\text{s}$）を 0s に平滑化、真のポーズ時は $+0.4\text{s}$ の呼吸余白を付与して画面をクリーンにクリア。
- **🎯 Netflix / YouTube 配信標準字幕品質監査エンジン（8大監査項目）**：
  - **1行文字数・表示幅制限**：日本語/中国語 $\le 15$ 字、韓国語 $\le 16$ 字、英語 $\le 42$ CPL（業界標準）。`--max-chars-cjk`、`--max-chars-korean`、`--max-chars-latin` で自由にカスタマイズ可能。
  - **読取速度監視 (CPS)**：日本語 $\le 6.0$ CPS、英語 $\le 20.0$ CPS。全編の平均 CPS とピーク CPS を算出し、Netflix 基準超過を警告リストへ登録。
  - **言語別最適化句読点ポリシー**：CJK（日本語/中国語/韓国語）は読点を空白化し行末句読点を完全除去。Latin（英語/フランス語/ドイツ語など）は文節のカンマ、ピリオド、コロン、セミコロン、発言遮断ダッシュ `—`、余韻三点リーダー `...` を正確に保持。
  - **多言語フォールバック機構**：`zh-TW`, `zh-CN`, `ja`, `ko`, `en` の5言語で専用語彙プロンプトを提供。未対応言語は英語ルールへ安全にフォールバック（初回のみ stderr 通知）。
  - **タイポグラフィ・書式クレンジング**：全角 `（）`、`【】`、`《》`、`「」` および半角括弧の整合性検証、漏洩した Markdown タグ（`**`、`_`、`` ` ``）の自動消去。
  - **長時間無音・無対話区間検出**：10 秒以上の無音区間（Gap $\ge 10.0\text{s}$）を検出し、B-roll、BGM、または ASR 脱落の確認用に前後のテキストとタイムコードを記録。
  - **音声開始 0 秒完全同期**：Whisper 物理音響開始点に厳格固定（0.000s）し、字幕のネタバレを防止。
  - **閲覧時間保護**：$1.0\text{s} \le \text{Duration} \le 6.0\text{s}$（短いフレーズは空白時間を活用して自動補正）。
  - **フリッカー防止微小ギャップ結合**：$< 0.2\text{s}$ の微小ギャップを 0s に平滑化、$+0.4\text{s}$ の呼吸余白を確保。
- **実行コマンド例**：
  ```bash
  # 標準実行（Google Cloud Vertex AI & ADC 認証 + GCS ステージング）：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # Stage 1 完了後に GCS 上の全編音声を即時削除：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --cleanup-gcs

  # 台本／収録原稿を渡して用語と文脈を最適化：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --script manuscript.txt

  # 字幕の行幅上限をカスタマイズ（デフォルト：英語 42 文字、日中 15 文字、韓国語 16 文字）：
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 \
    --language en --max-chars-latin 42 --max-chars-cjk 15 --max-chars-korean 16
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

## 🛠️ 必要環境とクラウド設定 (`setup.sh`)

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg**（`h264_videotoolbox` ハードウェアエンコードおよび `loudnorm` 対応）
- **Python 3.8+**（`numpy`, `google-genai`, `google-cloud-storage`）
- **Google Cloud SDK (`gcloud`)**

### ワンクリック環境構築 (`setup.sh`) と 2段階 GCS 自動クリーンアップ

本ツールキットは **100% Google Cloud Vertex AI（Application Default Credentials, ADC）+ Google Cloud Storage (GCS)** アーキテクチャを採用しており、API キーの手動管理や AI Studio へのアップロードは一切不要です。

```bash
# 対話形式でローカル依存関係チェック・ADC 認証・Vertex AI 有効化・GCS バケット構築を一括実行：
./setup.sh

# 非対話モード（Antigravity エージェント自動実行用）：
./setup.sh --project YOUR_GCP_PROJECT_ID --region us-central1 --non-interactive
```

1. **自動プロビジョニング内容 (`setup.sh`)**：
   - `aiplatform.googleapis.com`（Vertex AI）および `storage.googleapis.com`（GCS）API の自動有効化。
   - 専用 GCS バケット（`gs://multicam-video-${PROJECT_ID}`）の自動作成と IAM 権限（`roles/aiplatform.user`, `roles/storage.objectAdmin`）の設定。
   - `.env` 設定ファイルの自動生成（`GOOGLE_CLOUD_LOCATION=global`, `GCP_REGION=us-central1`）。
2. **🗑️ GCS バケット 2段階ライフサイクル自動削除ルール一覧**：

| GCS パスプレフィックス (`matchesPrefix`) | 保存対象ファイル | 保持期間 (`age`) | 削除メカニズム | 設計目的 |
| :--- | :--- | :--- | :--- | :--- |
| **`raw/audio_chunks/`** | 字幕 Stage 3 チャンク音声スライス (`.m4a` / `.mp3`) | **推論直後に即時削除** | Python `finally` で即時削除（`raw/` 2日ルールで二重保護） | チャンクごとのマルチモーダル音声校正が完了した直後に自動削除されます。 |
| **`raw/`** | 全編グリッド動画 (`multicam_merged_full.mp4`)、全編音声 (`final_cut_full_audio.m4a`) | **2日間 (`age: 2`)** | GCS Lifecycle 自動 `Delete`（`--cleanup-gcs` で即時削除可） | SHA-256 キャッシュにより同日中の再アップロードを防ぎつつ、**2日後**に自動削除してストレージ課金を抑止します。 |
| **`output/`**<br/>**`deliverables/`**<br/>**`multicam_assets/`** | タイムライン (`.xml` / `.csv`)、字幕 (`.srt` / `.vtt`)、レポート、完成動画などの成果物 | **15日間 (`age: 15`)** | GCS Lifecycle 自動 `Delete` | 成果物およびプロジェクト資産を **15日間** 保持し、チームのダウンロード・確認後に自動クリーンアップします。 |

```json
{
  "rule": [
    {
      "action": { "type": "Delete" },
      "condition": { "age": 2, "matchesPrefix": ["raw/"] }
    },
    {
      "action": { "type": "Delete" },
      "condition": { "age": 15, "matchesPrefix": ["output/", "deliverables/", "multicam_assets/"] }
    }
  ]
}
```
