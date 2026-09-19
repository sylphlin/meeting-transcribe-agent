# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 项目概览 (Overview)

**Meeting Transcribe Agent** 是基于 **Google Gemini 3.5 Transcribe** 与 **Gemini 3.8 Flash** 的多模态会议纪要与逐字稿生成系统。系统支持 YouTube 链接、本地视频文件、Google Drive 分享链接与纯音频录音。每次运行均会生成结构化 Markdown 会议纪要与独立的交互式 HTML 播放器。

### 三大专用处理管线

1. **YouTube 多模态云端管线 (Cloud Direct Ingestion)**：
   - **云端直接串流**：将 YouTube 链接直接发送至 **Gemini 3.8 Flash**，无需下载本地视频文件。
   - **Agentic 视频理解 (`--agentic`)**：自动浏览关键视频帧以识别演示文稿幻灯片、桌牌与字幕条（Lower-Thirds）。
   - **交互式 YouTube 播放器**：生成独立三栏式 HTML 播放器，支持逐字稿同步滚动与点击时间戳跳转。

2. **本地视频双阶段融合管线 (Acoustic Ground Truth + Vision Fusion)**：
   - **内嵌字幕提取**：自动检测视频容器中的内嵌字幕轨（`mov_text`, `srt`, `vtt`）或同名 `.srt` 字幕文件，作为参会名单与议程参考。
   - **Stage 0（术语表与语种检测）**：构建领域专业术语表，并检测主要口语 `BCP-47` 语言代码（例如 `cmn-Hant-TW`、`zh-CN`、`en-US`、`ja-JP`）。
   - **Stage 1（声学基准语音转录）**：提取 16 kHz 单声道音频，通过 **Gemini 3.5 Transcribe**（默认）或 **本地 Whisper + Sherpa-ONNX**（`--engine whisper`）执行语音转录，锁定物理时间戳 `[MM:SS - MM:SS]` 与说话人分段。
   - **Stage 2（多模态视觉融合与分块逐字稿校对）**：将 720p 视频与 Stage 1 逐字稿输入 **Gemini 3.8 Flash**。模型读取幻灯片画面与桌牌以生成第 1–5 节纪要，并以 60 行对话为单位并行校对第 6 节逐字稿的字形与专业术语，同时强制锁定原始时间戳。
   - **确定性说话人组装**：Python 程序依据字幕重叠、多模态时段规则与交接语义整合真实姓名与职务，确保零时间戳漂移。

3. **纯音频高精度管线 (Voice Recorders & Podcasts)**：
   - **Stage 0（术语表与语种检测）**：从音频提取专业术语并识别主要口语语言代码。
   - **Stage 1（声学语音转录）**：使用 **Gemini 3.5 Transcribe**（或离线 Whisper + Sherpa-ONNX）输出带时间戳的说话人对话。
   - **Stage 2（高管纪要合成与字形校对）**：使用 **Gemini 3.8 Flash** 生成高管摘要、行动项表，并并行校对第 6 节逐字稿字形与术语。

---

## 双引擎架构 (Dual-Engine Architecture)

### 1. 云端 Vertex AI 模式（`--engine gemini`，默认）
* **双模型协同**：由 **Gemini 3.5 Transcribe**（`TRANSCRIBE_MODEL`）负责声学语音转录，**Gemini 3.8 Flash**（`SUMMARY_MODEL`）负责多模态分析与分块校对。
* **智能音频预处理**：自动检测音频比特率，并将超过 25 分钟的长音频在静音边界处切分为 20 分钟分块并行转录。
* **确定性前缀锁定**：在第 6 节每个校对后的语句前强制还原 Stage 1 的 `[MM:SS - MM:SS] **spk_X**:` 前缀，杜绝时间戳偏移或输出截断。
* **GCS 双层生命周期管理**：暂存于 `gs://<bucket>/raw/` 的原始媒体在 2 天后自动删除，最终交付物保留 15 天。

### 2. 本地离线模式（`--engine whisper`，仅限显式指定）
* **显式启用机制**：仅在用户显式指定 `--engine whisper` 时启动，系统绝不自动降级或静默切换。
* **硬件加速**：支持 Apple Silicon GPU 原生加速（`mlx-whisper`）或跨平台 CPU/CUDA（`faster-whisper`）。
* **声学声纹聚类**：集成 **Sherpa-ONNX**（`eres2net` 或 `cam++`）提取本地声纹特征并对齐逐字时间戳。

---

## 安装与部署 (Installation & Deployment)

| 部署方式 | 目标环境 | 安装工具 | 主要操作界面 |
| :--- | :--- | :--- | :--- |
| **方式一：Google Antigravity & Agent Plugins** | 本地 IDE、Agent Skill 或 Python CLI | `pip` / `uv` + `./setup.sh` | Antigravity IDE 对话窗口或终端 CLI |
| **方式二：Gemini Enterprise** | Cloud Vertex AI Agent Runtime | `./deploy.sh`（原生 `gcloud`） | Gemini Enterprise Web UI、Agent Engine、A2A |

### 系统前置要求

1. **安装 FFmpeg**：
   - **macOS**：`brew install ffmpeg`
   - **Ubuntu / Debian**：`sudo apt update && sudo apt install ffmpeg`
   - **Windows**：`winget install Gyan.FFmpeg`

2. **配置 Google Cloud ADC 认证**：
   ```bash
   gcloud auth application-default login
   ```

### 方式一：Google Antigravity Plugin、Skill 与本地 CLI 安装

1. **克隆为 Agent Plugin（推荐）**：
   ```bash
   git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
   ```
2. **安装 Python 依赖包**：
   ```bash
   pip install google-genai google-cloud-storage requests
   ```
3. **初始化 Google Cloud 环境 (`./setup.sh`)**：
   ```bash
   chmod +x setup.sh
   ./setup.sh --project YOUR_GCP_PROJECT_ID
   ```

### 方式二：Gemini Enterprise 云端部署 (`./deploy.sh`)

```bash
uv tool install google-agents-cli
chmod +x setup.sh deploy.sh
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1
```

---

## 命令行使用说明 (Standalone CLI)

```bash
# YouTube 视频转录
python3 scripts/meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 本地纯音频转录（云端默认）
python3 scripts/meeting_transcribe.py "meeting_recording.mp3"

# 本地视频双阶段融合
python3 scripts/meeting_transcribe.py "conference_video.mp4"

# Google Drive 分享链接直连转录
python3 scripts/meeting_transcribe.py "https://drive.google.com/file/d/FILE_ID/view?usp=sharing"

# 显式指定离线 Whisper 模式
python3 scripts/meeting_transcribe.py "meeting_recording.mp3" --engine whisper --whisper-backend auto
```

---

## Google Drive 直连与 GCS 双层生命周期策略

| GCS 路径前缀 (`matchesPrefix`) | 存储对象 | 保留天数 (`age`) | 说明 |
| :--- | :--- | :--- | :--- |
| **`raw/`** | 暂存音频切片与 720p 视频 (`raw/<filename>`) | **2 天 (`age: 2`)** | 保留短期缓存供重复运行复用，满 2 天自动删除。 |
| **`minutes/`**、**`players/`**、**`output/`**、**`deliverables/`** | Markdown 会议纪要 (`.md`) 与交互式播放器 (`.html`) | **15 天 (`age: 15`)** | 保留最终交付物 15 天供团队审阅，到期自动清理。 |

---

## 许可证 (License)

本项目采用 [MIT License](LICENSE) 授权。
