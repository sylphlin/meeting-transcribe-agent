# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

## 📖 專案概述 (Overview)

**Meeting Transcribe Agent** 是一套基於 **Google Gemini 3.5 Transcribe** 打造的高精度多語者會議轉譯與智慧會議記錄 Agent。

### 為什麼採用「專用聲學 ASR ＋ 智慧語意 LLM」雙層架構？
在真實的多人會議場景中，單一模型往往難以兼顧「精確物理錨定」與「高層次語意理解」的矛盾痛點：
* **純通用大模型 (LLM) 的痛點**：若直接將音訊丟給通用多模態 LLM 轉譯，其時間戳記多為自回歸機率推估，長會議中極易產生**嚴重時間累積漂移、跳段漏句**，且無法依賴聲波頻譜進行嚴格的說話者分離。
* **傳統純語音辨識 (ASR) 的痛點**：僅能輸出未經校對的原始簡體或生硬字詞，缺乏語意脈絡理解能力，無法自動校正同音專有名詞、無法將發言者映射至真實官銜姓名，更無法提煉執行決策。

**我們的解決方案**：
本系統採用明確分工的雙層架構：
1. **底層聲學轉譯 (Gemini 3.5 Transcribe)**：專職毫秒級「詞級時間戳記 (Word Timestamps)」與物理聲學「語者分離 (Diarization)」，確保每一句話皆有真實聲波物理錨定，絕不跳漏。
2. **上層語意重構 (Gemini 3.8 Flash)**：專職前後文脈絡理解、同音專有名詞校正、發言人身分收斂與在地繁體化，並結構化提煉出決策摘要與待辦追蹤，最後渲染為獨立的雙欄互動播放器。

---

## 🎯 核心功能與適用場景

### 它能為您做什麼？
- 🎙️ **高精度語者區分與逐字轉譯**：原生支援說話者分群（Diarization）與詞級時間戳記，清楚標記每位與會者的發言起訖。
- ✍️ **前後文理解與地道繁體化**：超越死板字典轉換，透過 LLM 前後文理解自動校正同音錯字（如專有名詞、官銜），並將語音辨識內容流暢轉為標準繁體中文。
- 📋 **高管級結構化會議記錄**：自動提取會議基本資訊、執行摘要、重大決策事項表、討論議題分析與具體待辦追蹤事項（Action Items）。
- 🌐 **零外部依賴互動式 HTML 播放器**：產出單一輕量 HTML 檔案，支援點擊字句即時跳轉音訊、語者色彩標記、關鍵字即時搜尋與深淺主題切換。

### 適用場景
1. **公務機關與市政主管會議**：長達 1~2 小時、數十位局處長輪番發言，需精確紀錄案由、裁示要點並消除長音訊聲紋漂移。
2. **跨國企業與技術工程週會**：中英夾雜術語豐富（如 PR, Kubernetes, CI/CD, Roadmap），自動探勘並校正技術術語。
3. **訪談錄音、記者會與法務存證**：需完整保留每一句發言的真實原意與秒級時間戳，方便事後雙向對照音訊查證。
4. **機敏與隔離網閘環境 (Air-Gapped)**：具備離線備援引擎，可完全在本地 Apple Silicon GPU 或一般本機運作，語音絕不上雲。

---

## 🚀 雙引擎架構 (Dual-Engine Architecture)

### 1. 全雲端極速模式（預設核心）
* **雙模型架構**：採用 **Google Gemini 3.5 Transcribe**（多模態語音辨識與聲學切分）搭配 **Gemini 3.8 Flash**（結構化會議重構與摘要）。
* **智慧音訊預處理 (Smart Ingestion)**：自動探測音訊位元率與檔案體積。原始低碼率檔案免轉碼直傳，高碼率音訊則自適應壓縮至最佳串流品質，避免無謂的編碼與頻寬消耗。
* **雙軌非同步並行 (Dual-Track Concurrency)**：
  * 將「核心決策摘要」與「長篇逐字稿修復」拆分為兩個獨立軌道並行生成，徹底解決長篇文本循序輸出的阻塞問題。
  * 關閉思考預熱延遲（Zero Thinking Budget），實現即時的首字串流響應。
* **零殘留隱私保護**：音訊經由 Google Files API 傳輸，轉譯完成後自動呼叫清理機制銷毀雲端暫存，不留資料隱患。

### 2. 本地離線隱私模式（備援方案）
* **隔離網閘支援**：專為企業機房限制、Air-gapped 隔離網路或高度機敏語音設計，全流程本機執行。
* **跨平台硬體加速**：支援 Apple Silicon GPU 原生加速（`mlx-whisper`）或跨平台 CPU/CUDA（`faster-whisper`）。
* **聲學特徵向量聚類**：整合 **Sherpa-ONNX (3D-Speaker / PyAnnote)** 進行本機聲學特徵抽取與語者區分。
* **雙指針滑動窗口對齊 (Sliding Window)**：採用線性掃描算法實現單詞時間戳記與聲紋區間的毫秒級精確匹配，確保語者標記連續穩定。

---

## 🤖 Agent 對話使用指南 (推薦情境與 Prompt 範例)

本專案主要作為 **AI Agent Skill** 使用。您**不需要**手動輸入複雜的命令列參數，只需在對話框中向 Agent 提出需求，Agent 便會自主調度後台轉譯管線：

### 常用情境與對話範例：

1. **標準會議轉譯（全自動雲端極速處理）**：
   > 「請幫我轉譯這場會議錄音 `meeting.mp3`，整理出重點摘要、決策事項與逐字稿播放器。」

2. **搭配會議大綱／通知文件（強烈推薦：人名與術語最精準）**：
   > 「這是今天技術會議的錄音 `backend_sync.m4a`，旁邊附有會議通知 `agenda.md`。請幫我轉譯並校對人名職稱與專有名詞。」

3. **機敏會議離線轉譯（不連外網、純本地運作）**：
   > 「這份錄音 `internal_audit.m4a` 涉及機敏內容，請使用本地離線模式（Whisper + 聲紋切分）進行處理。」

4. **指定會議摘要語言（如跨國團隊需英文記錄）**：
   > 「Please transcribe `executive_call.mp3`. Keep the verbatim transcript in original languages, but generate the executive summary and action items in English.」

5. **公務或大型多人會議（長官身分正規化）**：
   > 「請幫我轉譯市政會議音檔 `council.mp3`，產出完整的各案由討論摘要、決策事項追蹤表，並自動將市長與局處首長的發言人標記正規化。」

---

## 🏗️ 核心處理流水線 (Pipeline Architecture)

```text
輸入：會議錄音檔 (.mp3 / .m4a / .wav 等) ＋ 可選參考文件（會議通知 / 議程大綱）
  │
  ├─▶ 階段 0：智慧音訊預處理 (Smart Audio Ingestion)
  │     └─ 探測音訊位元率與大小：低碼率免轉碼直傳，高碼率音訊進行自適應預壓縮
  │
  ├─▶ 階段 1：跨來源術語探勘 (Cross-Source Terminology Mining)
  │     └─ 結合外部議程文件解析與音訊首輪語義預掃描，提煉與會名單、官銜與技術術語對照表
  │
  ├─▶ 階段 2：語音轉譯與語者切分 (Speech Recognition & Diarization)
  │     ├─【雲端模式】Gemini 3.5 Transcribe 多模態辨識與自動切分（具備雲端暫存自動銷毀機制）
  │     └─【本地模式】Whisper 語音辨識 ＋ Sherpa-ONNX 聲學特徵向量聚類與滑動窗口對齊
  │
  ├─▶ 階段 3：雙軌非同步並行會議重構 (Dual-Track Concurrent Restructuring)
  │     ├─ 軌道 A（決策摘要）：提煉執行摘要、重大決策表、討論議題分析與待辦事項清單
  │     ├─ 軌道 B（逐字稿精修）：基於上下文語義校正同音錯字，並將辨識內容轉為標準繁體中文
  │     └─ 發言人收斂與平滑：正規化角色名稱，並將同一發言人的連續停頓語句自動平滑合併
  │
  └─▶ 階段 4：成果發布與播放器生成 (Artifact Generation & Delivery)
        ├─ 輸出標準 Markdown 格式的完整結構化會議記錄
        └─ 注入數據至獨立 HTML 模板，生成零外部依賴的雙欄互動播放器
```

### 產出成果檔案：
每次轉譯完成後，會在音訊同層目錄自動產出：
1. **📄 `<檔案名>_會議記錄.md`**：完整結構化會議記錄（基本資訊、重點摘要、專題討論、決策事項、待辦追蹤與帶時間戳記發言逐字稿）。
2. **🌐 `<檔案名>_player.html`**：獨立零外部依賴的**雙欄互動式音訊審閱播放器**。
3. **📚 `<檔案名>_glossary.md`**：**全域權威術語與人員對照表**（若有啟用探勘）。

---

## 📦 安裝與環境設定 (Quick Setup)

### 1. 系統依賴 (FFmpeg)
用於音訊格式探測與預壓縮：
- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt update && sudo apt install ffmpeg`
- **Windows**: `winget install Gyan.FFmpeg`

### 2. 安裝 Python 套件

**雲端預設模式套件**：
```bash
pip install google-genai
```

**離線備援模式可選套件（如需使用本機 Whisper 與聲紋切分）**：
```bash
# Apple Silicon Mac (推薦 GPU 加速)
pip install mlx-whisper sherpa-onnx

# 通用平台 (CPU / NVIDIA CUDA)
pip install faster-whisper sherpa-onnx
```

### 3. 設定 Gemini API Key
從 [Google AI Studio](https://aistudio.google.com/) 取得 API Key，並設定環境變數：
```bash
export GEMINI_API_KEY="AIzaSy..."
```
*(亦可填入工作區的 `.env` 或 `~/.gemini/.env` 檔案中，系統會自動載入)*

### 4. 安裝為 Agent Skill
本專案符合通用 Agent Skill 規格，可直接安裝至您的 AI 工作區：
- **Google Antigravity 全域技能**：
  ```bash
  git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/skills/meeting-transcribe-agent
  ```
- **工作區專屬技能 (Workspace Skill)**：
  ```bash
  git clone https://github.com/sylphlin/meeting-transcribe-agent.git .agent/skills/meeting-transcribe-agent
  ```
- **Claude Code / Cursor / Windsurf**：
  Clone 至相應平台的 skills 目錄（如 `~/.claude/skills/`）即可自動被 Agent 索引調用。

---

## 📂 專案目錄結構

```text
meeting-transcribe-agent/
├── SKILL.md                          # Agent Skill 專用作業手冊與參數架構
├── README.md                         # 專案介紹、使用情境與技術架構
├── LICENSE                           # MIT 開源授權
├── .gitignore                        # 忽略測試音訊與本機快取
├── .env.example                      # API Key 環境變數範例
├── meeting_transcribe.py             # 根目錄命令列入口
├── scripts/                          # 核心模組
│   ├── __init__.py
│   ├── meeting_transcribe.py         # 主流程調度器 (支援雙引擎)
│   ├── audio_utils.py                # 智慧碼率探測與 FFmpeg 預壓縮
│   ├── gemini_engine.py              # Gemini 3.5 Transcribe 轉譯與 3.8 Flash 雙軌重構
│   ├── diarization.py                # 本地聲學切分 (Sherpa-ONNX) 與滑動窗口對齊
│   ├── glossary.py                   # 雙軌專有名詞探勘
│   ├── canonicalizer.py              # 聲學分群收斂與語者正規化
│   └── html_generator.py             # 現代獨立 HTML 播放器生成器
└── assets/                           # 播放器模板與提示詞
    ├── player_template.html          # 互動式會議播放器模板
    └── prompts/                      # 提示詞模板目錄
```

---

## 💻 進階：開發者與命令列呼叫 (Developer & Headless CLI)

> [!TIP]
> **一般使用者注意**：如果您是透過 AI Agent（如 Antigravity / Claude Code）使用本系統，您**不需要手動輸入這些命令**！Agent 會依據對話自動閱讀 `SKILL.md` 並配置最佳參數。
>
> 以下內容僅供開發者進行本機除錯、批次腳本自動化或無頭伺服器排程參考：

### 基本執行（雲端預設）
```bash
python3 meeting_transcribe.py "會議錄音.mp3"
```

### 本地離線備援執行（Apple Silicon GPU / Sherpa-ONNX）
```bash
python3 meeting_transcribe.py "會議錄音.mp3" --engine whisper --whisper-backend auto
```

### 帶會議大綱與指定輸出語言
```bash
python3 meeting_transcribe.py "會議錄音.mp3" --outline "agenda.txt" --summary-language en
```

### 完整參數手冊

| 參數 | 說明 | 預設值 |
| :--- | :--- | :--- |
| `audio_file` | 音訊檔案路徑 (支援 mp3, m4a, wav, mp4, aac, flac 等) | *(必填)* |
| `-o, --output` | 自訂 Markdown 會議記錄輸出路徑 | `<檔名>_會議記錄.md` |
| `--engine` | 轉譯引擎：`gemini` (雲端預設) 或 `whisper` (本地離線備援) | `gemini` |
| `--whisper-backend` | 離線模式後端：`auto` (自動偵測 Apple Silicon MLX), `mlx`, `faster-whisper` | `auto` |
| `--whisper-model` | 離線模式模型大小 (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `--no-diarization` | 停用離線模式的聲學聲紋切分 | `False` |
| `--clustering-threshold` | Sherpa-ONNX 聲學聚類閥值 | `0.68` |
| `--num-speakers` | 精確與會發言人人數（已知時填寫，-1 為自動偵測） | `-1` |
| `--embedding-type` | Sherpa-ONNX 聲紋特徵抽取模型 (`eres2net`, `pyannote`, `cam++`) | `eres2net` |
| `--api-key` | 手動指定 Gemini API Key (預設讀取 `GEMINI_API_KEY`) | `None` |
| `--transcribe-model` | 雲端轉譯語音辨識模型 | `gemini-3.5-transcribe` |
| `--summary-model` | 結構化會議記錄與摘要生成模型 | `gemini-3.8-flash` |
| `--outline` | 外部會議通知、大綱或議程檔案路徑 (.txt / .md) | `None` |
| `--force-glossary` | 強制重新提取全域術語對照表 (覆蓋快取) | `False` |
| `--no-glossary` | 跳過全域術語對照表提取 | `False` |
| `--no-player` | 停用獨立互動式 HTML 播放器生成 | `False` |
| `--no-compress` | 停用上傳前 FFmpeg 自動預壓縮 | `False` |
| `--summary-language` | 指定會議記錄語言 (`auto` 自動跟隨對話；或 `en`, `zh-TW`, `ja` 等) | `None` (auto) |
| `--only-transcript` | 僅執行第一階段轉譯輸出純逐字稿，跳過結構化摘要 | `False` |
| `--language` | 離線 Whisper 語音語言代碼 (`auto`, `en`, `zh`, `ja`) | `auto` |

---

## 📄 授權條款 (License)

本專案採用 [MIT License](LICENSE) 開源授權。

