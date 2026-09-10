# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 📖 プロジェクト概要 (Overview)

**Meeting Transcribe Agent** は、**Gemini 3.5 Transcribe** および **Gemini Agentic Video Understanding** を基盤に構築された、次世代の映像・音声会議録生成 AI エージェントです。「マルチモーダル動画処理（YouTube URL / ローカル動画）」と「高精度音声2層構造パイプライン」を兼ね備え、自治体の市政会議、多国籍チームの技術定例会、インタビューや法務証言録取など、「ミリ秒単位の正確なタイムスタンプ」「高精度な話者分離」「構造化された議事録作成」が求められる専門的な現場向けに設計されています。

### メディアに応じたインテリジェントな2系統パイプライン：

1. **🎥 マルチモーダル動画パイプライン (YouTube リンク / ローカル動画ファイル)**：
   - **単一リクエストによる圧倒的な処理効率**：**Gemini 3.8 Flash** によるエンドツーエンドの視覚・音声マルチモーダル分析。プレゼン資料（Slide OCR）、席札、ニュースのテロップ字幕を同時に読み取り、発言者の氏名・役職を100%の精度で特定。処理時間とトークン消費を50%削減（32分の動画を約44秒で処理）。
   - **Agentic Video Understanding (`--agentic`)**：動的なマルチターンフレーム探索とツール呼び出しにより、数時間に及ぶ長編動画や複雑な図表スライドを徹底的に分析。
   - **ピクチャー・イン・ピクチャー対応 YouTube プレイヤー**：生成される単一 HTML プレイヤーに YouTube IFrame コントローラーを内蔵。文字起こしテキストの秒数クリックによるシークとカラオケ風リアルタイムハイライトに対応。

2. **🎙️ 高精度音声パイプライン (ボイスレコーダー / ポッドキャスト / 音声ファイル)**：
   - **下層：音響文字起こし (Gemini 3.5 Transcribe)**：ミリ秒単位の「単語レベルタイムスタンプ (Word Timestamps)」と物理音響に基づく「話者ダイアライゼーション (Diarization)」を担当し、発言の聞き漏らしやスキップを完全に防止。
   - **上層：文脈的構造化 (Gemini 3.8 Flash)**：文脈理解による同音異義語・専門用語の補正、話者名の正規化、自然な言語表現への洗練を行い、決定事項やToDoリストを自動抽出。
   - **ローカルオフライン備え**：Apple Silicon GPU (MLX) / faster-whisper と Sherpa-ONNX 音響声紋クラスタリングによるローカル実行をサポート。

---

### 💡 なぜ「動画（YouTube / ローカル動画）」と「純粋な音声」を分離するのか？

会議の文字起こしにおいて、「映像がある状態」と「音声のみ」では情報密度に根本的な違いが存在します：

1. **動画モード（視覚的コンテキストの保持・話者特定率 100%）**：
   - **視覚 OCR による話者固定**：会議にはデスクのネームプレート、放送字幕、投影スライドなどの重要情報が含まれます。音声を抽出して音声のみの処理に落とし込むと、名乗らずに発言した参加者や役職者を特定できなくなります。
   - **単一リクエストによる極限の効率化**：動画を直接マルチモーダル LLM（Gemini 3.8 Flash）に渡すことで、文字情報と音声を一度に融合理解し、「ASR 変換後に LLM へ再送する」二重の通信遅延とトークン消費を回避します。
2. **純粋音声モード（音響物理分離・最大のトークン節約）**：
   - **画面がない場合の音響物理的アンカー**：ボイスレコーダーやポッドキャストには映像が一切ありません。この場合、専用の音響モデル（Gemini 3.5 Transcribe / オフライン Whisper + Sherpa-ONNX）が厳密なタイムスタンプと声紋分離を提供します。
   - **圧倒的な低コスト**：純粋な音声は毎秒約32トークンしか消費しないため、長時間の録音を最小限のコストで処理できます。

---

### 📊 トークン消費量の目安（実測に基づく経験値）

> [!NOTE]
> 実際のトークン消費量は、発言密度、画面の動き、スライドの細かさによって変動します。以下の倍率は、長編会議の実測に基づく参考値（経験則）です：

* **1. 音声2層パイプライン (Pure Audio Pipeline) — `~1x` 基準消費**:
  - **仕組みと消費量**: 音声は1秒あたり約32トークン（1時間の音声で数万〜十数万トークン程度）。
  - **位置づけ**: **最も経済的でトークンを大幅節約**。画面が不要なボイスレコーダー、ポッドキャスト、インタビュー等に特化し、専用の音響モデルが単語タイムスタンプと話者分離を厳密に処理。
* **2. Gemini Agentic Video Understanding — `~2x` 消費**:
  - **仕組みと消費量**: Google の最新技術 [Gemini Agentic Video](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/) を採用。消費量は音声パイプラインの**約2倍程度**。
  - **位置づけ**: **本プロジェクトが推奨する動画の深層理解モード（`--agentic`）**。全フレームを無差別に処理するのではなく、思考キャッシュ（Thinking Cache）と動的なツール呼び出しを組み合わせ、必要な瞬間のみ高解像度フレームを探索。数時間の長編会議や複雑な図表スライドに最適。
* **3. Traditional Gemini Video Understanding — `~3x` 消費**:
  - **仕組みと消費量**: 従来の動画マルチモーダルでは毎秒1フレーム（1 FPS Uniform Sampling）を固定サンプリングするため、トークン消費量が最大（音声の**3倍以上**）。
  - **位置づけ**: **【本プロジェクトでは非採用・比較対照用】**。固定周期サンプリングは静止画面や冗長なフレームを大量に送信し、トークンと待ち時間を浪費します。本プロジェクトは YouTube 直接解析および Agentic 探索技術により、この従来手法を刷新しました。

---

## 🎯 主な機能と活用シーン

### コア機能
- 📺 **YouTube リンク＆動画ファイルの直接サポート**：URL または動画パスを指定するだけで、構造化議事録と再生プレイヤーを一括生成。
- 👁️ **視覚的なネームプレート＆スライド OCR 認識**：テロップ字幕やスライドから、登壇者の本名や公職名を自動特定。
- 🎙️ **高精度な話者分離と発言タイムスタンプ**：発言者ごとの発言区間と名前を正確に整理。
- ✍️ **文脈理解と自然な文章表現**：LLM の前後関係把握により、専門用語や同音異義語を自動補正し、自然な文章に整形。
- 📋 **エグゼクティブ向け構造化議事録**：基本情報、エグゼクティブサマリー、重要決定事項、議題別分析、アクションアイテム（ToDo）を網羅。
- 🌐 **外部依存ゼロのインタラクティブ HTML プレイヤー**：クリックで該当秒数へジャンプ、話者ごとの色分け、リアルタイム検索、多言語UI切り替えに対応した単一 HTML ファイルを出力。

### 活用シーン
1. **自治体・官公庁の公開会議**：YouTube ライブ配信の市政会議などをダウンロード不要で直接読み込み、役職者の名札を認識して議事録化。
2. **長編ウェビナー・技術カンファレンス**：投影スライドと音声を統合し、技術的要点を効率的に要約。
3. **対面での役員会議・多人数ディスカッション**：多数の発言者が入れ替わる長時間の音声でも、声紋のブレを抑えて記録。
4. **ローカル音声認識のバックアップ**：ネットワーク制限時やローカル処理が必要な場合に、オフライン Whisper によるバックアップ処理が可能。

---

## 🚀 デュアルエンジン設計 (Dual-Engine Architecture)

### 1. クラウド高速モード（デフォルト）
* **2段階モデル連携**：**Google Gemini 3.5 Transcribe**（音響認識・話者分離）＋ **Gemini 3.8 Flash**（構造化・議事録作成）。
* **スマート音声前処理 (Smart Ingestion)**：ビットレートを自動判定。低ビットレートはそのまま送信し、高ビットレートは最適な 16kHz mono へ自動圧縮。
* **デュアルトラック並行処理**：「要約・決定事項」と「長文逐字録の校正」を非同期並行で生成し、待機時間を大幅に短縮。
* **完全プライベート（ゼロ保持）**：Google Files API 経由のアップロードデータは、処理完了後に `finally` 処理で自動削除。

### 2. ローカル音声認識バックアップモード（Whisper + Sherpa-ONNX）
* **オフライン音響認識**：クラウド接続が制限されている環境で、第1段階の文字起こしと話者分離をローカルモデルで実行。
* **ハードウェアアクセラレーション**：Apple Silicon GPU (`mlx-whisper`) または CPU/CUDA (`faster-whisper`) に対応。
* **声紋ベクトルクラスタリング**：**Sherpa-ONNX (3D-Speaker / PyAnnote)** によるローカル声紋抽出。
* **スライディングウィンドウ整列**：単語タイムスタンプと声紋区間をミリ秒精度で正確にマッチング。

---

## 🤖 エージェント対話プロンプト集 (Prompt Guide)

本プロジェクトは **AI Agent Skill** としての利用を前提としています。複雑なコマンドを手動入力する必要はなく、エージェントへ自然言語で指示するだけで自動実行されます：

### よく使われるプロンプト例：

1. **📺 YouTube 動画の会議書き起こし（名札・スライドの視覚認識）**：
   > 「YouTube の市政会議 `https://www.youtube.com/watch?v=Xff98Q5bki8` を文字起こしして、画面のネームプレートやスライドを参考に構造化議事録とプレイヤーを作成してください。」

2. **🤖 YouTube 長編会議の深層探索（Agentic Video Understanding）**：
   > 「この 3 時間の YouTube カンファレンス `https://www.youtube.com/watch?v=...` について、Agentic Video モードを使って重要なスライドを探索し、各スピーカーの結論をまとめてください。」

3. **🎥 ローカル動画ファイルの処理（スライド内容の抽出）**：
   > 「会議動画 `tech_summit.mp4` を文字起こししてください。スライド画面も確認して、専門用語と話者名を正しく反映してください。」

4. **⚡ 動画から音声を抽出して高速処理（トークン節約）**：
   > 「この動画 `interview.mp4` は定点カメラなので、音声を抽出して純粋音声モードで処理し、トークンを節約して要約を作成してください。」

5. **🎙️ 標準的な音声会議の文字起こし（クラウド高速処理）**：
   > 「会議録音 `meeting.mp3` を文字起こしして、重要要約、決定事項、逐字録プレイヤーを生成してください。」

6. **📑 アジェンダ／次第の事前読み込み（推奨：名前・専門用語の精度向上）**：
   > 「技術会議の録音 `backend_sync.m4a` と次第 `agenda.md` です。参加者の役職や専門用語を照合しながら文字起こししてください。」

7. **🌐 議事録出力言語の指定（多国籍チーム向け）**：
   > 「Please transcribe `executive_call.mp3`. Keep the verbatim transcript in original languages, but generate the executive summary and action items in Japanese.」

---

## 🏗️ 処理パイプライン概要 (Pipeline Architecture)

```mermaid
flowchart TD
    classDef inputStyle fill:#2D3748,stroke:#4A5568,stroke-width:2px,color:#fff;
    classDef routerStyle fill:#D69E2E,stroke:#B7791F,stroke-width:2px,color:#fff;
    classDef videoStyle fill:#2B6CB0,stroke:#2C5282,stroke-width:2px,color:#fff;
    classDef audioStyle fill:#2C7A7B,stroke:#234E52,stroke-width:2px,color:#fff;
    classDef outputStyle fill:#276749,stroke:#1C4532,stroke-width:2px,color:#fff;

    subgraph Input["📥 入力ソース (Multi-Source Input)"]
        Y["YouTube URL (Watch / Shorts / Live)"]:::inputStyle
        V["ローカル動画ファイル (.mp4 / .mov / .mkv)"]:::inputStyle
        A["音声ファイル (.mp3 / .m4a / .wav / .aac)"]:::inputStyle
        O["会議次第・資料 (任意 --outline)"]:::inputStyle
    end

    Router{"メディア判定<br>(Smart Router)"}:::routerStyle

    Y --> Router
    V --> Router
    A --> Router

    subgraph VideoTrack["🎥 マルチモーダル動画パイプライン"]
        VMode{"モード選択"}:::videoStyle
        Static["⚡ 静的フレームモード (デフォルト 1 FPS)<br>• トークン: ~3x<br>• 約44秒で高速完了 / OCR精度100%"]:::videoStyle
        Agentic["🤖 Agentic Video (--agentic)<br>• トークン: ~2x<br>• 動的フレーム探索＆深層分析"]:::videoStyle
        GeminiFlash["Google Gemini 3.8 Flash<br>(単一リクエストによる一括分析)"]:::videoStyle
        VisionOCR["視覚 OCR 連携：<br>• 席札 / 登壇者氏名・役職<br>• 放送字幕テロップ<br>• プレゼンスライド (Slide OCR)"]:::videoStyle

        VMode -- "デフォルト" --> Static --> GeminiFlash
        VMode -- "フラグ --agentic" --> Agentic --> GeminiFlash
        GeminiFlash <--> VisionOCR
    end

    subgraph AudioTrack["🎙️ 音声2層パイプライン"]
        Ingest["スマート前処理<br>(ビットレート判定 / FFmpeg 圧縮)"]:::audioStyle
        ASREngine{"音声認識エンジン"}:::audioStyle
        GTranscribe["【クラウド】Gemini 3.5 Transcribe<br>• 単語レベルタイムスタンプ<br>• 音響話者分離 (Diarization)<br>• トークン: ~1x (最も経済的)"]:::audioStyle
        OfflineWhisper["【ローカル】MLX / Faster-Whisper<br>+ Sherpa-ONNX 声紋クラスタリング"]:::audioStyle
        Restructure["【文脈構造化】Gemini 3.8 Flash<br>• 同音異義語＆専門用語補正<br>• 話者統合＆自然な日本語化"]:::audioStyle

        Ingest --> ASREngine
        ASREngine -- "クラウド (デフォルト)" --> GTranscribe --> Restructure
        ASREngine -- "オフライン (--engine whisper)" --> OfflineWhisper --> Restructure
        O -. 文脈を注入 .-> Ingest
    end

    Router -- "動画または YouTube" --> VMode
    Router -- "音声 (または --extract-audio)" --> Ingest

    subgraph Delivery["📦 成果物出力とプレイヤー"]
        MD["📄 構造化議事録.md<br>(基本情報 / 要約 / 決定事項 / ToDo / 逐字録)"]:::outputStyle
        HTML["🌐 単一完結インタラクティブプレイヤー.html"]:::outputStyle
        YTDock["🎬 YouTube PiP ウィンドウ<br>(正確なシーク＆カラオケ同期)"]:::outputStyle
        AudioPlayer["🎵 ネイティブ音声プレイヤー<br>(波形・タイムスタンプジャンプ)"]:::outputStyle

        MD --> HTML
        HTML --> YTDock
        HTML --> AudioPlayer
    end

    GeminiFlash --> MD
    GeminiFlash -. YouTube 動画を読み込み .-> YTDock
    Restructure --> MD
    Restructure -. 音声を読み込み .-> AudioPlayer
```

### 🔄 パイプラインの詳細ステップ

#### ステップ 1：入力メディアの自動判定 (Smart Router)
- **YouTube URL** または **動画ファイル**（`.mp4`, `.mov`, `.mkv`）：**🎥 動画パイプライン**へ自動ルーティング。
- **音声ファイル**（`.mp3`, `.m4a`, `.wav` 等）または `--extract-audio` 指定時：**🎙️ 音声パイプライン**へルーティング。

#### ステップ 2A：動画マルチモーダル処理 (YouTube & ローカル動画)
1. **クラウド直接解析 / 最適化**：
   - **YouTube**：API へ直接 URL を渡し、ダウンロード不要で解析（429制限を回避）。
   - **ローカル動画**：大容量ファイルは自動で 720p H.264 に軽量圧縮してアップロード（完了後即時削除）。
2. **単一リクエストによる一括解析**：
   - `gemini-3.8-flash` が視覚 OCR（名札・テロップ・スライド）と音声を同時に把握。
   - 1回の呼び出しで、正確な話者名付きの議事録と逐字録を完成させます。

#### ステップ 2B：音声2層処理 (純粋な音声録音)
1. **スマート前処理**：ビットレートを判定し、必要に応じて 16kHz mono に自動変換。
2. **用語・アジェンダの事前マイニング**：次第ファイル（`--outline`）から固有名詞を事前抽出。
3. **下層：音響文字起こし**：Gemini 3.5 Transcribe またはローカル Whisper＋Sherpa-ONNX で精密な単語タイムスタンプと話者分離を実施。
4. **上層：文脈構造化**：Gemini 3.8 Flash で同音異義語を補正し、決定事項とToDoを整理。

#### ステップ 3：成果物の出力 (Delivery)
- **構造化議事録 Markdown**：`<ファイル名>_會議記錄.md` として保存。
- **インタラクティブ HTML プレイヤー**：`<ファイル名>_player.html` を出力。YouTube 映像との同期や、音声再生バー、カラオケハイライトを標準搭載。

---

## 📦 インストールと環境構築

### 1. システム依存関係 (FFmpeg)
音声変換および動画圧縮に使用：
- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt update && sudo apt install ffmpeg`
- **Windows**: `winget install Gyan.FFmpeg`

### 2. Python パッケージのインストール

**クラウド通常モード**：
```bash
pip install google-genai
```

**ローカルオフライン備えモード（任意）**：
```bash
# Apple Silicon (M1/M2/M3/M4) GPU
pip install mlx-whisper sherpa-onnx soundfile numpy

# Linux / Windows / Intel Mac
pip install faster-whisper sherpa-onnx soundfile numpy
```

---

## ⚙️ 環境変数の設定

Gemini API キーを設定します：

```bash
# macOS / Linux
export GEMINI_API_KEY="your-gemini-api-key"

# Windows PowerShell
$env:GEMINI_API_KEY="your-gemini-api-key"
```

---

## 💻 コマンドライン実行ガイド

> [!NOTE]
> AI Agent Skill として使用する場合、手動でコマンドを実行する必要はありません。チャットでエージェントに依頼するだけで完了します！

### 基本的な実行（YouTube 動画）
```bash
# YouTube 動画の直接文字起こし＆プレイヤー生成
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=Xff98Q5bki8"

# Agentic Video Understanding による動的フレーム探索の有効化
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=Xff98Q5bki8" --agentic
```

### 基本的な実行（音声およびローカルファイル）
```bash
# クラウドデフォルトモード
python3 meeting_transcribe.py "meeting_record.mp3"

# ローカルオフラインバックアップ実行（Apple Silicon GPU / Sherpa-ONNX）
python3 meeting_transcribe.py "meeting_record.mp3" --engine whisper --whisper-backend auto
```

### 主な引数一覧

| 引数 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `input_source` | 音声/動画ファイルパス または YouTube URL | *(必須)* |
| `-o, --output` | 出力先 Markdown ファイルパス | `<ファイル名>_會議記錄.md` |
| `--agentic` | 動画の Agentic Video 理解モードを有効化 | `False` |
| `--extract-audio` | 動画から音声を抽出して音声パイプラインで処理 | `False` |
| `--engine` | 音声認識エンジン：`gemini` (クラウド) または `whisper` (ローカル) | `gemini` |
| `--whisper-backend` | オフラインバックエンド：`auto`, `mlx`, `faster-whisper` | `auto` |
| `--whisper-model` | Whisper モデルサイズ (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `--no-diarization` | 話者分離を無効化 | `False` |
| `--clustering-threshold` | Sherpa-ONNX クラスタリング閾値 | `0.68` |
| `--num-speakers` | 参加人数（既知の場合指定、-1 は自動検出） | `-1` |
| `--api-key` | Gemini API キーの手動指定 | `None` |
| `--outline` | 会議通知・次第ファイルパス (.txt / .md) | `None` |
| `--no-player` | インタラクティブ HTML プレイヤーの出力を無効化 | `False` |
| `--summary-language` | 議事録の出力言語指定 (`auto`, `ja`, `en`, `zh-TW` 等) | `None` (auto) |

---

## 📄 ライセンス

本プロジェクトは [MIT License](LICENSE) のもとで公開されています。
