# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 概要 (Overview)

**Meeting Transcribe Agent** は、**Google Gemini 3.5 Transcribe** と **Gemini 3.8 Flash** を基盤としたマルチモーダル会議議事録および全文文字起こし生成システムです。YouTube URL、ローカル動画ファイル、Google Drive 共有リンク、および音声ファイルに対応しています。実行ごとに `<input_dir>/output/` サブディレクトリへ構造化された Markdown 議事録とスタンドアロン型のインタラクティブ HTML プレーヤーを自動的に分離出力します。

### 3つの専用処理パイプライン

1. **YouTube マルチモーダル・クラウドパイプライン (Cloud Direct Ingestion)**：
   - **クラウド直接ストリーミング**：ローカルに動画をダウンロードせず、YouTube URL を直接 **Gemini 3.8 Flash** へ送信します。
   - **Agentic 動画理解**：重要な映像フレームを動的に参照し、スライド、卓上ネームプレート、画面テロップ（Lower-Thirds）の OCR 認識を実行します。
   - **インタラクティブ YouTube プレーヤー**：文字起こしの同期スクロールとタイムスタンプ・シークに対応した 3 ペイン HTML プレーヤーを生成します。

2. **ローカル動画 2 段階融合パイプライン (Acoustic Ground Truth + Vision Fusion)**：
   - **埋め込み字幕の抽出**：動画コンテナ内の字幕トラック（`mov_text`, `srt`, `vtt`）または `.srt` ファイルを検出し、参加者リストや議題のリファレンスとして活用します。
   - **Stage 0（専門用語集＆言語コード検出）**：ドメイン用語集を構築し、主要な音声言語の `BCP-47` コード（`ja-JP`, `cmn-Hant-TW`, `en-US` など）を自動検出します。
   - **Stage 1（音響基準 ASR 文字起こし）**：16 kHz モノラル音声を抽出し、**Gemini 3.5 Transcribe**（デフォルト）または **ローカル Whisper + Sherpa-ONNX**（オフラインモード指定時）で物理タイムスタンプ `[MM:SS - MM:SS]` と話者分離を確定します。
   - **Stage 2（マルチモーダル視覚融合＆チャンク校正）**：大容量動画（>250 MB）を Apple Silicon `VideoToolbox` ハードウェアアクセラレーションにより 720p H.264（`10 fps`、`1 秒 GOP -g 10`、`+faststart`、`libx264` 自動フォールバック対応）へ高速圧縮してクラウド転送および Agentic フレーム探索を高速化し、Stage 1 の文字起こしとともに **Gemini 3.8 Flash** に入力します。スライドやネームプレートから実名・役職を特定してセクション 1〜5 を作成し、セクション 6（全文記録）を 60 行単位の並列バッチで表記・専門用語校正します（タイムスタンプは厳密に維持されます）。

3. **純音声・高精度パイプライン (Voice Recorders & Podcasts)**：
   - **Stage 0（専門用語集＆言語コード検出）**：音声から専門用語と主要言語コードを抽出します。
   - **Stage 1（音響文字起こし）**：**Gemini 3.5 Transcribe**（またはオフライン Whisper + Sherpa-ONNX）でタイムスタンプ付き発話を作成します。
   - **Stage 2（要約生成＆表記校正）**：**Gemini 3.8 Flash** によりエグゼクティブサマリーとアクションアイテムを生成し、全文文字起こしの表記を校正します。

---

## デュアルエンジン・アーキテクチャ (Dual-Engine Architecture)

### 1. クラウド Vertex AI モード（デフォルト）
* **モデル連携**：音響文字起こしに **Gemini 3.5 Transcribe**（`TRANSCRIBE_MODEL`）、視覚分析・要約・校正に **Gemini 3.8 Flash**（`SUMMARY_MODEL`）を使用します。
* **アダプティブ音声前処理**：25 分を超える長時間音声は無音区間で 20 分チャンクに分割し、並列で文字起こしを実行します。
* **タイムスタンプ接頭辞ロック**：セクション 6 の各発話に対して Stage 1 の `[MM:SS - MM:SS] **spk_X**:` 接頭辞をプログラムで再結合し、時刻ずれや出力途切れを防止します。
* **GCS 2 階層ライフサイクル管理**：`gs://<bucket>/raw/` の一時メディアは **2 日後** に自動削除され、生成された成果物は **15 日間** 保持されます。

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
