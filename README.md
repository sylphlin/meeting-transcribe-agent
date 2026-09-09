# Meeting Transcribe Agent - 泛用型會議記錄與語音轉譯 Agent Skill
### Universal Meeting Intelligence & Interactive Transcription Suite (Cloud-Scale & Offline Backup)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skills Spec](https://img.shields.io/badge/Agent%20Skills-Spec%20Compliant-00C853.svg)](https://agentskills.io/specification)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)

一套專為高精度會議轉譯、聲學說話者切分（Speaker Diarization）、術語探勘與結構化會議記錄生成設計的 **Agent Skill**。通用於各類企業會議、技術工程週會、訪談紀錄、公開演說與公務會議。

遵循開放的 **[Agent Skills 規格標準 (agentskills.io)](https://agentskills.io/specification)**，原生相容於 Google Antigravity、Claude Code、Cursor、Windsurf 等各大 AI Agent 開發環境，支援在轉譯完成後自動喚起瀏覽器展示成果。

---

## 雙引擎架構 (Dual-Engine Architecture)

- **預設核心（全雲端模式 `--engine gemini`）**：
  - 核心採用 **Google Gemini 3.5 Transcribe** 搭配 **Gemini 3.7 Flash**。
  - 極速轉譯（1 小時音訊約 30~60 秒完成），原生單詞級時間戳記與語者切分。
  - Google Files API 自動暫存與**零殘留自動銷毀**（`client.files.delete`），無持續雲端儲存費用與資料隱私疑慮。
- **備援備案（離線本地模式 `--engine whisper`）**：
  - 本地離線執行：支援 Apple Silicon GPU 加速（`mlx-whisper`）或跨平台 CPU/CUDA（`faster-whisper`）。
  - 聲學聲紋分離：整合新世代 **Sherpa-ONNX (3D-Speaker / PyAnnote)** 本地聲學聲紋聚類與重疊語音偵測。
  - 專為**企業機房 IP 限制、隔離網閘 (Air-gapped) 或機敏無法上雲情境**設計。

---

## 🤖 Agent 對話使用指南 (推薦情境與 Prompt 範例)

本專案主要作為 **Agent Skill** 由 AI Assistant（如 Google Antigravity、Claude Code 等）直接調用。使用者只需在對話框中輸入自然的日常語言，Agent 便會自主識別參數、執行轉譯管線，並自動為您彈出互動式審閱播放器：

### 常用呼叫語句範例：

1. **基本會議轉譯與整理（雲端極速預設）**：
   > 「請幫我轉譯這場會議錄音 `meeting.mp3`，整理出重點摘要與逐字稿，完成後幫我開啟播放器。」

2. **搭配會議大綱/通知提升術語與人名精準度**：
   > 「這是今天下午技術會議的錄音 `backend_sync.m4a`，旁邊附有會議通知 `agenda.md`。請幫我轉譯並校準專有名詞與發言人身分。」

3. **離線/本地模式轉譯（機房網路受限或機敏資料）**：
   > 「這份錄音 `internal_audit.m4a` 屬於機敏內容，請使用本地離線模型（Whisper + Sherpa-ONNX）進行轉譯與聲紋切分，完成後開啟播放器。」

4. **跨語言生成摘要（例如英文會議紀錄）**：
   > 「Please transcribe `executive_call.mp3`. Keep the verbatim transcript in original languages, but generate the executive summary and action items in English, then open the interactive player.」

5. **公務或大型多講者會議（消除發言人身分漂移）**：
   > 「請幫我轉譯市政會議音檔 `council.mp3`，產出完整的各案由討論摘要、決策事項追蹤表，並自動將市長與局處首長的發言人標記正規化。」

---

## 🏗️ 核心處理流水線 (Pipeline Architecture)

Meeting Transcribe Agent 採用四階段高精度流水線，將多模態大模型轉譯與長音訊聲學漂移收斂深度結合：

```text
音訊輸入 (.mp3 / .m4a / .wav) + 可選會議大綱 (.txt / .md)
  │
  ├─▶ 階段 1：雙軌專有名詞探勘 (Dual-Track Glossary Mining)
  │     └─ 百萬上下文輕量預掃描 + 議程解析 ──▶ 提煉權威術語與人員官銜對照表
  │
  ├─▶ 階段 2：語音辨識與聲學切分 (Speech Recognition & Diarization)
  │     ├─【預設】雲端 Gemini 3.5 Transcribe：極速多模態辨識 + 零暫存自動銷毀
  │     └─【備援】本地 Whisper + Sherpa-ONNX：純離線聲紋特徵聚類與語音重疊偵測
  │
  ├─▶ 階段 3：語義仲裁與語者身分收斂 (Canonical Speaker Consolidation)
  │     ├─ 綜合語境判定真實講者身分，解決長音訊特徵漂移 (將 spk_2, spk_3, spk_7 自動收斂為單一講者)
  │     └─ 自動合併前後 2.0 秒內同講者的連貫語句，杜絕過度切音造成的閱讀破碎感
  │
  └─▶ 階段 4：現代化獨立互動播放器 (Modern Web Guidance UI)
        ├─ 產出完全零外部依賴的獨立 HTML 播放器
        └─ 自動喚起系統預設瀏覽器 (Zero-Click Auto-Open)
```

### 核心特性亮點：
- **極速與資料隱私兼備**：預設雲端模式透過 Google 官方 Files API 傳輸，轉譯完畢後**自動銷毀雲端暫存檔**，免除自建 Storage Bucket 的維護與長期存儲外洩風險。
- **聲學分群漂移收斂 (Canonical Consolidation)**：公務會議長達 1~2 小時，發言人常因情緒高低、距離麥克風遠近等因素產生聲紋特徵向量偏移。系統透過語義推理仲裁與時間窗口平滑，自動將分散的聲學群集收斂為單一權威身分。
- **動態語言跟隨 (Dynamic Language Adaptation)**：會議記錄的主旨、討論重點與待辦清單會自動跟隨使用者的對話語言（繁體中文、English、日本語等），而逐字稿嚴格保留原生發言內容，兼顧閱讀便利與法規存證真實性。
- **唱片級雙欄互動播放器**：獨立 HTML5 檔案，左欄公文記錄、右欄逐字稿。支援單詞點擊跳轉、發言中卡片平滑滾動置頂、即時關鍵字搜尋高亮、深淺色主題切換與一鍵複製富文本至 Google Docs。

---

## 📦 安裝與環境設定 (Quick Setup)

### 1. 系統環境依賴
- **Python** 3.10 或以上
- **FFmpeg**（用於音訊最佳化預壓縮與時長探測）：
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt update && sudo apt install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg`

### 2. 安裝 Python 套件

**雲端預設模式核心套件**：
```bash
pip install google-genai
```

**離線備援模式可選套件（如需使用本機 Whisper 與聲紋切分）**：
```bash
# Apple Silicon Mac (推薦 GPU 加速)
pip install mlx-whisper sherpa-onnx

# 或通用平台 (CPU / NVIDIA CUDA)
pip install faster-whisper sherpa-onnx
```

### 3. 設定 Gemini API Key (全雲端預設模式)
從 [Google AI Studio](https://aistudio.google.com/) 取得 API Key，並設定環境變數：
```bash
export GEMINI_API_KEY="AIzaSy..."
```
*(亦可填入工作區的 `.env` 或 `~/.gemini/.env` 檔案中，系統會自動載入)*

---

## 🔌 Agent Skills 規格安裝 (Cross-Agent Support)

本專案完全符合開放的 **[Agent Skills Specification (agentskills.io)](https://agentskills.io/specification)** 規範，可原生無縫安裝於各大 AI Agent：

### 1. Google Antigravity
- **全域技能 (Global Skill)**：
  ```bash
  git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/skills/meeting-transcribe-agent
  ```
- **專案專用 (Workspace Skill)**：
  ```bash
  git clone https://github.com/sylphlin/meeting-transcribe-agent.git .agent/skills/meeting-transcribe-agent
  ```

### 2. Claude Code / Cursor / Windsurf
直接將本倉庫 clone 至各大平台支援的 skills 目錄（如 `~/.claude/skills/`）即可自動被索引與調用。

---

## 💻 CLI 開發者命令列手冊 (Developer Reference)

如果需要在終端機手動批次處理音訊或除錯，可直接透過命令列調用：

### 基本執行（雲端預設）
```bash
python3 meeting_transcribe.py "會議錄音.mp3" --open
```

### 本地離線備援執行（Apple Silicon GPU / Sherpa-ONNX）
```bash
python3 meeting_transcribe.py "會議錄音.mp3" --engine whisper --whisper-backend auto --open
```

### 帶會議大綱與指定輸出語言
```bash
python3 meeting_transcribe.py "會議錄音.mp3" --outline "agenda.txt" --summary-language en --open
```

### 參數一覽

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
| `--summary-model` | 結構化會議記錄與摘要生成模型 | `gemini-3.7-flash` |
| `--outline` | 外部會議通知、大綱或議程檔案路徑 (.txt / .md) | `None` |
| `--force-glossary` | 強制重新提取全域術語對照表 (覆蓋快取) | `False` |
| `--no-glossary` | 跳過全域術語對照表提取 | `False` |
| `--no-player` | 停用獨立互動式 HTML 播放器生成 | `False` |
| `--no-compress` | 停用上傳前 FFmpeg 自動預壓縮 | `False` |
| `--summary-language` | 指定會議記錄語言 (`auto` 自動跟隨對話；或 `en`, `zh-TW`, `ja` 等) | `None` (auto) |
| `--open` | 轉譯完成後自動以預設瀏覽器開啟 HTML 播放器 | `False` |

### 產出檔案
每次轉譯完成後，會在音訊同層目錄自動產出成果：
1. **📄 `<檔案名>_會議記錄.md`**：完整結構化會議記錄（基本資訊、重點摘要、專題討論、決策事項、待辦追蹤與帶時間戳記發言逐字稿）。
2. **🌐 `<檔案名>_player.html`**：獨立零外部依賴的**雙欄互動式音訊審閱播放器**。
3. **📚 `<檔案名>_glossary.md`**：**全域權威術語與人員對照表**（若有啟用探勘）。

---

## 📂 專案目錄結構

```text
meeting-transcribe-agent/
├── SKILL.md                          # Agent Skill 標準規格手冊 (YAML frontmatter)
├── README.md                         # 專案說明、Agent Prompt 範例與技術架構
├── LICENSE                           # MIT 開源授權
├── .gitignore                        # 忽略測試音訊、成果檔案與本機暫存
├── .env.example                      # API Key 環境變數範例檔
├── meeting_transcribe.py             # 根目錄 CLI 入口 (CLI Forwarder)
├── scripts/                          # 模組化核心功能
│   ├── __init__.py
│   ├── meeting_transcribe.py         # 主轉譯流程調度器 (支援雙引擎與 --open)
│   ├── audio_utils.py                # 音訊處理工具 (時長偵測、格式化與 FFmpeg 預壓縮)
│   ├── gemini_engine.py              # Gemini 3.5 轉譯 (Files API + 自動銷毀) 與會議記錄生成
│   ├── diarization.py                # 本地聲學聲紋切分 (Sherpa-ONNX) 與 Whisper/MLX 轉譯
│   ├── glossary.py                   # 雙軌專有名詞探勘 (音訊預掃描 + 大綱摘要)
│   ├── canonicalizer.py              # 聲學分群收斂、語者正規化與連貫發言合併
│   └── html_generator.py             # 輕量 Markdown 解析器與現代 HTML 播放器渲染引擎
└── assets/                           # 靜態資源與外部模板
    ├── player_template.html          # 互動式會議播放器 HTML/CSS/JS
    └── prompts/                      # 提示詞模板目錄 (Markdown 格式)
        ├── minutes_prompt.md         # Stage 2 會議記錄與逐字稿提示詞
        └── audio_glossary_prompt.md  # Track 1 音訊預掃描術語提示詞
```

---

## 📄 授權條款 (License)

本專案採用 [MIT License](LICENSE) 開源授權。
