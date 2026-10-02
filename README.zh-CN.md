# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 项目概览 (Overview)

**Meeting Transcribe Agent** 是基于 **Google Gemini 3.5 Transcribe** 与 **Gemini 3.8 Flash** 的多模态会议纪要与逐字稿生成系统。系统支持 YouTube 链接、本地视频文件、Google Drive 分享链接与纯音频录音。每次运行均会在 `<input_dir>/output/` 隔离目录中自动生成结构化 Markdown 会议纪要与独立的交互式 HTML 播放器，保持原始媒体目录整洁。

### 三大专用处理管线

1. **YouTube 多模态云端管线 (Cloud Direct Ingestion)**：
   - **云端直接串流**：将 YouTube 链接直接发送至 **Gemini 3.8 Flash**，无需下载本地视频文件。
   - **Agentic 视频理解**：自动浏览关键视频帧以识别演示文稿幻灯片、桌牌与字幕条（Lower-Thirds）。
   - **交互式 YouTube 播放器**：生成独立三栏式 HTML 播放器，支持逐字稿同步滚动与点击时间戳跳转。

2. **本地视频双阶段融合管线 (Acoustic Ground Truth + Vision Fusion)**：
   - **内嵌字幕提取**：自动检测视频容器中的内嵌字幕轨（`mov_text`, `srt`, `vtt`）或同名 `.srt` 字幕文件，作为参会名单与议程参考。
   - **Stage 0（术语表与语种检测）**：构建领域专业术语表，并检测主要口语 `BCP-47` 语言代码（例如 `cmn-Hant-TW`、`zh-CN`、`en-US`、`ja-JP`）。
   - **Stage 1（声学基准语音转录）**：提取 16 kHz 单声道音频，通过 **Gemini 3.5 Transcribe**（默认）或 **本地 Whisper + Sherpa-ONNX**（显式指定离线模式时）执行语音转录，锁定物理时间戳 `[MM:SS - MM:SS]` 与说话人分段。
   - **Stage 2（多模态视觉融合与分块逐字稿校对）**：大型视频（>250 MB）自动通过 Apple Silicon `VideoToolbox` 硬件加速压缩为 720p H.264（`10 fps`、`1 秒 GOP -g 10`、`+faststart`，支持 `libx264` 自动降级）以加速云端上传与 Agentic 幻灯片抽帧，并连同 Stage 1 逐字稿输入 **Gemini 3.8 Flash**。模型读取幻灯片画面与桌牌以生成第 1–5 节纪要，并以 60 行对话为单位并行校对第 6 节逐字稿的字形与专业术语，同时强制锁定原始时间戳。
   - **确定性说话人组装**：Python 程序依据字幕重叠、多模态时段规则与交接语义整合真实姓名与职务，确保零时间戳漂移。

3. **纯音频高精度管线 (Voice Recorders & Podcasts)**：
   - **Stage 0（术语表与语种检测）**：从音频提取专业术语并识别主要口语语言代码。
   - **Stage 1（声学语音转录）**：使用 **Gemini 3.5 Transcribe**（或离线 Whisper + Sherpa-ONNX）输出带时间戳的说话人对话。
   - **Stage 2（高管纪要合成与字形校对）**：使用 **Gemini 3.8 Flash** 生成高管摘要、行动项表，并并行校对第 6 节逐字稿字形与术语。

---

## 双引擎架构 (Dual-Engine Architecture)

### 1. 云端 Vertex AI 模式（默认）
* **双模型协同**：由 **Gemini 3.5 Transcribe**（`TRANSCRIBE_MODEL`）负责声学语音转录，**Gemini 3.8 Flash**（`SUMMARY_MODEL`）负责多模态分析与分块校对。
* **智能音频预处理**：自动检测音频比特率，并将超过 25 分钟的长音频在静音边界处切分为 20 分钟分块并行转录。
* **确定性前缀锁定**：在第 6 节每个校对后的语句前强制还原 Stage 1 的 `[MM:SS - MM:SS] **spk_X**:` 前缀，杜绝时间戳偏移或输出截断。
* **GCS 双层生命周期管理**：暂存于 `gs://<bucket>/raw/` 的原始媒体在 2 天后自动删除，最终交付物保留 15 天。

### 2. 本地离线模式（仅限显式指定）
* **显式启用机制**：仅在用户在对话中明确要求“本地/离线转录”时启动，系统绝不自动降级或静默切换。
* **硬件加速**：支持 Apple Silicon GPU 原生加速（`mlx-whisper`）或跨平台 CPU/CUDA（`faster-whisper`）。
* **声学声纹聚类**：集成 **Sherpa-ONNX**（`eres2net` 或 `cam++`）提取本地声纹特征并对齐逐字时间戳。

---

## 安装与部署 (Installation & Deployment)

| 部署方式 | 目标环境 | 安装工具 | 主要操作界面 |
| :--- | :--- | :--- | :--- |
| **方式一：Google Antigravity & Agent Plugins** | 本地 Antigravity IDE 与 Agent Skill | `pip` / `uv` + `./setup.sh` | Antigravity IDE 对话窗口 |
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

### 方式一：Google Antigravity Plugin 与 Skill 安装

1. **克隆为 Agent Plugin（推荐）**：
   ```bash
   git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
   ```
   - **旧版独立 Skill 目录安装（`~/.gemini/config/skills/` 兼容方式）**：
     ```bash
     git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
     ln -s ~/.gemini/config/plugins/meeting-transcribe-agent/skills/meeting-transcribe-agent ~/.gemini/config/skills/meeting-transcribe-agent
     ```
2. **安装 Python 依赖包**：
   ```bash
   pip install google-genai google-cloud-storage requests
   ```
3. **初始化 Google Cloud 环境 (`./setup.sh`)**：
   ```bash
   cd ~/.gemini/config/plugins/meeting-transcribe-agent
   chmod +x setup.sh
   ./setup.sh --project YOUR_GCP_PROJECT_ID
   ```

### 方式二：Gemini Enterprise 云端部署 (`./deploy.sh`)

```bash
uv tool install google-agents-cli
chmod +x setup.sh deploy.sh
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1
```

### 项目目录结构（Agent Plugins 1.0 标准规范）
- **SSOT 实体目录**：`skills/meeting-transcribe-agent/`（内含 `SKILL.md` Agent 专用 CLI 参数手册、`scripts/` 与 `assets/`），无根目录冗余软链接。
- **双层 `AGENTS.md` 规范**：根目录 `AGENTS.md` 定义工作区与工程开发规范（Part I & Part II），`rules/AGENTS.md` 随 Plugin 打包注入 AI 客户端执行期守则（只读与 Fail-Fast）。

---

## Antigravity 操作方式与使用场景 (Usage & Scenarios)

在 Antigravity 中，您可以通过以下两种方式操作 **Meeting Transcribe Agent**：

1. **极简指令（`/` 指定技能 + `@` 标记文件，推荐）**：输入 `/meeting-transcribe-agent` 选择技能，并用 `@` 标记音频、视频或议程文件，只需列出关键字段（如 `文件: @XX, 议程: @YY`），无需多余说明。
2. **口语表达（自然语言自动触发）**：直接用日常口语描述转录与纪要需求，Antigravity 会自动识别意图并调用该 Plugin。

### 场景 1：纯音频会议录音转录与结构化纪要
- **极简指令**：
  ```text
  /meeting-transcribe-agent 文件: @meeting_recording.mp3, 主题: Executive_Board_Meeting, 参会人: John Doe, Jane Smith
  ```
- **口语表达**：
  ```text
  帮我把 @meeting_recording.mp3 转成会议纪要与逐字稿，主题是“Executive_Board_Meeting”，参会人有 John Doe 和 Jane Smith。
  ```

### 场景 2：本地视频或 YouTube 演讲转录（含幻灯片与桌牌视觉识别）
- **极简指令**：
  ```text
  /meeting-transcribe-agent 视频: @conference_video.mp4, 语言: 简体中文
  ```
  *（YouTube 链接写法：`/meeting-transcribe-agent 链接: https://www.youtube.com/watch?v=VIDEO_ID, 语言: 简体中文`）*
- **口语表达**：
  ```text
  请分析 @conference_video.mp4 的画面幻灯片与讲者发言，生成会议纪要与交互式播放器。
  ```

### 场景 3：配合会议议程校正专业术语与职务
- **极简指令**：
  ```text
  /meeting-transcribe-agent 文件: @meeting_recording.mp3, 议程: @agenda.md
  ```
- **口语表达**：
  ```text
  请参考 @agenda.md 的议程与名单，将 @meeting_recording.mp3 转成逐字稿与会议纪要。
  ```

### 场景 4：跨语种会议纪要（第 6 节保留原音，第 1–5 节指定语言）
- **极简指令**：
  ```text
  /meeting-transcribe-agent 文件: @meeting_recording.mp3, 摘要语言: 简体中文
  ```
- **口语表达**：
  ```text
  请转录这场英文会议 @meeting_recording.mp3，第 6 节保留英文原音逐字稿，第 1 到 5 节摘要与行动项请用中文撰写。
  ```

### 场景 5：机密会议指定本地离线模式转录
- **极简指令**：
  ```text
  /meeting-transcribe-agent 文件: @meeting_recording.mp3, 模式: 本地离线转录, 说话人数: 4
  ```
- **口语表达**：
  ```text
  这场会议请用本地离线模式转录 @meeting_recording.mp3，现场共有 4 位参会人。
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
