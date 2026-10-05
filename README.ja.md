# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Cloud STT v2 Chirp 3](https://img.shields.io/badge/Cloud%20STT%20v2-Chirp%203-orange.svg)](https://cloud.google.com/speech-to-text)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 概要 (Overview)

**Meeting Transcribe Agent** は、**Google Cloud Speech-to-Text v2 デュアルパス Chirp 3 (`chirp_3`)** と **Gemini 3.8 Flash (`gemini-3.8-flash`)** を基盤としたマルチモーダル会議議事録および全文文字起こし生成システムです。YouTube URL、ローカル動画ファイル、Google Drive 共有リンク、および音声ファイルに対応しています。実行ごとに `<input_dir>/output/` サブディレクトリへ構造化された Markdown 議事録とスタンドアロン型のインタラクティブ HTML プレーヤーを自動的に分離出力します。

### 3つの専用処理パイプライン

1. **YouTube マルチモーダル・クラウドパイプライン (Cloud Direct Ingestion)**：
   - **クラウド直接ストリーミング**：ローカルに動画をダウンロードせず、YouTube URL を直接 **Gemini 3.8 Flash** へ送信します。
   - **Agentic 動画理解**：重要な映像フレームを動的に参照し、スライド、卓上ネームプレート、画面テロップ（Lower-Thirds）の OCR 認識を実行します。
   - **インタラクティブ YouTube プレーヤー**：文字起こしの同期スクロールとタイムスタンプ・シークに対応した 3 ペイン HTML プレーヤーを生成します。

2. **ローカル動画 2 段階融合パイプライン (Acoustic Ground Truth + Vision Fusion)**：
   - **埋め込み字幕の抽出**：動画コンテナ内の字幕トラック（`mov_text`, `srt`, `vtt`）または `.srt` ファイルを検出し、参加者リストや議題のリファレンスとして活用します。
   - **Stage 0（専門用語集＆言語コード検出）**：ドメイン用語集を構築し、主要な音声言語の `BCP-47` コード（`ja-JP`, `cmn-Hant-TW`, `en-US` など）を自動検出します。
   - **Stage 1（デュアルパス Chirp 3 音響基準 ASR 文字起こし）**：16 kHz モノラル MP3 音声を抽出し、**デュアルパス Cloud Speech-to-Text v2 Chirp 3 (`chirp_3`)**（デフォルト）または **ローカル Whisper + Sherpa-ONNX**（オフラインモード指定時）でグローバル話者分離（`Speaker 1`, `Speaker 2`）と物理単語タイムスタンプ `[MM:SS - MM:SS]` を確定します。
   - **Stage 2（マルチモーダル視覚融合・意味段落分割＆チャンク校正）**：大容量動画（>250 MB）を Apple Silicon `VideoToolbox` ハードウェアアクセラレーションにより 720p H.264（`10 fps`、`1 秒 GOP -g 10`、`+faststart`、`libx264` 自動フォールバック対応）へ高速圧縮してクラウド転送および Agentic フレーム探索を高速化し、Stage 1 の文字起こしとともに **Gemini 3.8 Flash** に入力します。スライドやネームプレートから実名・役職を特定してセクション 1〜5 を作成し、セクション 6（全文記録）を 60 行単位の並列バッチで表記校正および長尺発話の意味段落分割（`<PARA>`）を行い、単語タイムスタンプへ再投影します。

3. **純音声・高精度パイプライン (Voice Recorders & Podcasts)**：
   - **Stage 0（専門用語集＆言語コード検出）**：音声から専門用語と主要言語コードを抽出します。
   - **Stage 1（デュアルパス Chirp 3 音響文字起こし）**：**デュアルパス Chirp 3 (`chirp_3`)**（またはオフライン Whisper + Sherpa-ONNX）でグローバル話者分離とタイムスタンプ付き発話を作成します。
   - **Stage 2（要約生成・意味段落分割＆表記校正）**：**Gemini 3.8 Flash** によりエグゼクティブサマリーとアクションアイテムを生成し、全文文字起こしの表記校正と意味段落分割を実行します。

---

### デュアルパス Chirp 3 (Dual-Pass Chirp 3) アーキテクチャの目的

従来の長時間音声のチャンク分割文字起こしには、2つの構造的な課題がありました：
1. **チャンク間の話者ラベルリセットと司会引き継ぎの結合**：15 分ごとに音声を分割するとチャンク境界で話者 ID（`spk_0`, `spk_1`）がリセットされ、司会者が次の発言者を紹介する箇所で同一話者として結合される問題が発生します。
2. **Cloud STT v2 単語タイムスタンプの 20 分制限**：Google Cloud Speech-to-Text v2 (`chirp_3`) は `enableWordTimeOffsets=True` の場合インライン処理が最大 20 分に制限されますが、`enableWordTimeOffsets=False` の場合は最大 8 時間の音声を分割せずに一括処理し、一貫したグローバル話者分離を維持できます。

**「数時間にわたるグローバルな話者一貫性」**と**「ミリ秒単位の単語タイムスタンプ」**を両立するため、Stage 1 では 2 つのトラックを並列実行します：
- **Track A — グローバル話者分離 (Macro Global Diarization)**：分割なしの完全な 16 kHz モノラル音声に対して単一の `BatchRecognize`（`enableSpeakerDiarization=True`、`enableWordTimeOffsets=False`）を実行し、会議全体で単一の話者空間を維持します。
- **Track B — 単語タイムスタンプ抽出 (Micro Word Timestamps)**：音声を 5 秒のオーバーラップを持つ 18 分（`1080s`）チャンクに分割して並列実行（`enableWordTimeOffsets=True`、`enableSpeakerDiarization=False`）し、オーバーラップ中央値で重複排除して単調増加タイムスタンプを保証します。
- **Track B 物理タイムライン主軸 + LCS 話者ラベル割り当て (`AlignmentEngine`)**：Track B の物理的な単語タイムスタンプを主タイムラインとして保持し、CJK 文字単位＋欧文単語単位のハイブリッド最長共通部分列（LCS）アルゴリズムで Track A の話者ラベルを各単語に割り当てます（Track A は照合前に簡体字から繁体字へ変換）。FFmpeg `silencedetect` で物理的な発話開始点を検出し、先頭無音区間内のノイズトークンを除去します。発話区間の開始は `floor`、終了は `ceil` を使用し、シークが前の話者の末尾に入らないようにします。両トラックに n-gram 反復ループ抑制を適用します。
- **Stage 2 意味段落分割とタイムスタンプ再投影**：数分間に及ぶ独演に対して、**Gemini 3.8 Flash** が話題の転換点で `<PARA>` マーカーを挿入し、`AlignmentEngine` が各意味段落の開始・終了時刻を物理単語タイムスタンプから再計算します。インタラクティブ HTML プレーヤーでは同一話者の連続段落を視覚的に連結（`isGrouped`）して表示します。

---

## デュアルエンジン・アーキテクチャ (Dual-Engine Architecture)

### 1. クラウド デュアルパス Chirp 3 + Vertex AI モード（デフォルト）
* **モデル連携**：音響話者分離と単語タイムスタンプに **Cloud Speech-to-Text v2 Chirp 3**（`TRANSCRIBE_MODEL=chirp_3`、Chirp シリーズのみ）、視覚分析・要約・意味段落分割・校正に **Gemini 3.8 Flash**（`SUMMARY_MODEL=gemini-3.8-flash`）を使用します。
* **デュアルパス並列実行**：Track A（非分割グローバル話者分離）と Track B（18 分並列チャンク単語タイムスタンプ＋5 秒オーバーラップ重複排除）を同時実行します。
* **物理タイムライン上の決定論的話者割り当て**：Track B の単語タイムスタンプを主軸とし、ハイブリッド LCS で Track A の話者ラベルを割り当て、FFmpeg 発話開始点より前のトークンを除去し、Stage 2 の意味段落を物理単語境界へ再投影します。
* **GCS 2 階層ライフサイクル管理**：`gs://<bucket>/raw/` の一時メディアは処理完了時に `finally` ブロックで即時削除（および 2 日ライフサイクルルールで自動削除）され、生成された成果物は **15 日間** 保持されます。

### 2. ローカル・オフラインモード（明示的要求のみ）
* **明示的有効化**：ユーザーが対話内で「ローカル/オフライン文字起こし」を明示的に要求した場合のみ起動します（クラウドエラー時に無断でローカルへフォールバックすることはありません）。
* **ハードウェアアクセラレーション**：Apple Silicon GPU（`mlx-whisper`）および CPU/CUDA（`faster-whisper`）に対応します。
* **声紋クラスタリング**：**Sherpa-ONNX**（`eres2net` / `cam++`）で声紋特徴量を抽出し、単語タイムスタンプと話者区間を照合します。

---

## インストールとデプロイ (Installation & Deployment)

| 方法 | 対象環境 | セットアップツール | 主なインターフェース |
| :--- | :--- | :--- | :--- |
| **方法 1：Google Antigravity & Agent Plugins** | ローカル Antigravity IDE、Agent Skill | `pip` / `uv` + `./setup.sh` | Antigravity IDE チャット |
| **方法 2：Gemini Enterprise** | Cloud Vertex AI Agent Runtime | `./deploy.sh`（ネイティブ `gcloud`） | Gemini Enterprise Web UI、Agent Engine、A2A |

### システム前提条件

1. **FFmpeg のインストール**：
   - **macOS**：`brew install ffmpeg`
   - **Ubuntu / Debian**：`sudo apt update && sudo apt install ffmpeg`
   - **Windows**：`winget install Gyan.FFmpeg`

2. **Google Cloud ADC 認証**：
   ```bash
   gcloud auth application-default login
   ```

### 方法 1：Google Antigravity Plugin / Skill セットアップ

1. **Agent Plugin としてクローン（推奨）**：
   ```bash
   git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
   ```
   - **従来の単一 Skill ディレクトリへのインストール（`~/.gemini/config/skills/` 互換）**：
     ```bash
     git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
     ln -s ~/.gemini/config/plugins/meeting-transcribe-agent/skills/meeting-transcribe-agent ~/.gemini/config/skills/meeting-transcribe-agent
     ```
2. **Python 依存パッケージのインストール**：
   ```bash
   pip install google-genai google-cloud-storage requests
   ```
3. **Google Cloud 環境の初期化 (`./setup.sh`)**：
   ```bash
   cd ~/.gemini/config/plugins/meeting-transcribe-agent
   chmod +x setup.sh
   ./setup.sh --project YOUR_GCP_PROJECT_ID
   ```

### 方法 2：Gemini Enterprise クラウドデプロイ (`./deploy.sh`)

```bash
uv tool install google-agents-cli
chmod +x setup.sh deploy.sh
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1
```

### ディレクトリ構造（Agent Plugins 1.0 準拠）
- **SSOT 実体ディレクトリ**：`skills/meeting-transcribe-agent/`（エージェント専用 CLI リファレンスを含む `SKILL.md`、`scripts/`、`assets/` を格納）を単一の信頼できる情報源とします。
- **2 層 `AGENTS.md` 構成**：ルートの `AGENTS.md` は開発・エンジニアリング規約（Part I & Part II）を定義し、`rules/AGENTS.md` はプラグインに同梱される AI クライアント実行時ルール（読み取り専用、Fail-Fast）を定義します。

---

## Antigravity での操作方法と利用シナリオ (Usage & Scenarios)

Antigravity では、以下の 2 つの方法で **Meeting Transcribe Agent** を操作できます。

1. **簡潔なコマンド指定（`/` でスキル選択 + `@` でファイル指定、推奨）**：`/meeting-transcribe-agent` を入力してプラグインを選択し、`@` でファイルを添付します。`ファイル: @XX, 議題: @YY` のように主要項目だけを指定すれば、文章を書く必要はありません。
2. **自然言語プロンプト（自動ルーティング）**：日常の言葉で文字起こしや議事録作成を指示すると、Antigravity が自動的にこのプラグインを選択して実行します。

### シナリオ 1：音声会議の録音から構造化議事録とオフラインプレーヤーを生成
- **簡潔な `/ + @` コマンド**：
  ```text
  /meeting-transcribe-agent ファイル: @meeting_recording.mp3, 議題: Executive_Board_Meeting, 参加者: John Doe, Jane Smith
  ```
- **自然言語プロンプト**：
  ```text
  @meeting_recording.mp3 を文字起こしして、議題「Executive_Board_Meeting」、参加者 John Doe と Jane Smith で議事録とインタラクティブプレーヤーを作成してください。
  ```

### シナリオ 2：ローカル動画または YouTube 講演の文字起こし（スライド＆ネームプレート OCR）
- **簡潔な `/ + @` コマンド**：
  ```text
  /meeting-transcribe-agent 動画: @conference_video.mp4, 言語: 日本語
  ```
  *（YouTube リンクの場合：`/meeting-transcribe-agent URL: https://www.youtube.com/watch?v=VIDEO_ID, 言語: 日本語`）*
- **自然言語プロンプト**：
  ```text
  @conference_video.mp4 の画面上のスライドと発言内容を分析し、日本語の議事録とインタラクティブプレーヤーを作成してください。
  ```

### シナリオ 3：会議アジェンダ・資料を用いた専門用語と役職の照合
- **簡潔な `/ + @` コマンド**：
  ```text
  /meeting-transcribe-agent ファイル: @meeting_recording.mp3, アジェンダ: @agenda.md
  ```
- **自然言語プロンプト**：
  ```text
  @agenda.md の議題と参加者リストを参照して、@meeting_recording.mp3 の議事録と全文文字起こしを作成してください。
  ```

### シナリオ 4：多言語会議の要約（セクション 6 は原語維持、セクション 1〜5 は指定言語）
- **簡潔な `/ + @` コマンド**：
  ```text
  /meeting-transcribe-agent ファイル: @meeting_recording.mp3, 要約言語: 日本語
  ```
- **自然言語プロンプト**：
  ```text
  @meeting_recording.mp3 を文字起こししてください。セクション 6 は元の発話言語を維持し、セクション 1〜5 の要約とアクションアイテムは日本語で作成してください。
  ```

### シナリオ 5：機密会議のローカル・オフライン文字起こし
- **簡潔な `/ + @` コマンド**：
  ```text
  /meeting-transcribe-agent ファイル: @meeting_recording.mp3, モード: ローカルオフライン, 話者数: 4
  ```
- **自然言語プロンプト**：
  ```text
  @meeting_recording.mp3 をローカルオフラインモード（話者 4 名）で文字起こししてください。
  ```

---

## Google Drive 連携と GCS ライフサイクルポリシー

| GCS パス接頭辞 (`matchesPrefix`) | 保存対象 | 保持期間 (`age`) | 目的 |
| :--- | :--- | :--- | :--- |
| **`raw/`** | 一時音声チャンクおよび 720p 動画 (`raw/<filename>`) | **2 日間 (`age: 2`)** | 再実行時のキャッシュ再利用のために保持し、2 日経過後に自動削除します。 |
| **`minutes/`**、**`players/`**、**`output/`**、**`deliverables/`** | Markdown 議事録 (`.md`)、HTML プレーヤー (`.html`) | **15 日間 (`age: 15`)** | チームでの確認用に 15 日間保持した後、自動削除します。 |

---

## ライセンス (License)

本プロジェクトは [MIT License](LICENSE) の下で提供されています。
