# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 개요 (Overview)

**Meeting Transcribe Agent**는 **Google Gemini 3.5 Transcribe**와 **Gemini 3.8 Flash**를 기반으로 구축된 멀티모달 회의록 및 전문 녹취록 생성 시스템입니다. YouTube URL, 로컬 비디오 파일, Google Drive 공유 링크 및 오디오 녹음 파일을 처리합니다. 실행할 때마다 `<input_dir>/output/` 하위 디렉터리에 구조화된 Markdown 회의록과 독립 실행형 인터랙티브 HTML 플레이어를 자동으로 격리 생성합니다.

### 3가지 전용 처리 파이프라인

1. **YouTube 멀티모달 클라우드 파이프라인 (Cloud Direct Ingestion)**:
   - **클라우드 직접 스트리밍**: 로컬 비디오 다운로드 없이 YouTube URL을 **Gemini 3.8 Flash**로 직접 전송합니다.
   - **Agentic 비디오 이해**: 주요 비디오 프레임을 탐색하여 발표 슬라이드, 명패, 화면 하단 자막(Lower-Thirds)을 OCR로 인식합니다.
   - **인터랙티브 YouTube 플레이어**: 녹취록 동기화 스크롤 및 타임스탬프 탐색을 지원하는 3단 HTML 플레이어를 생성합니다.

2. **로컬 비디오 2단계 융합 파이프라인 (Acoustic Ground Truth + Vision Fusion)**:
   - **내장 자막 추출**: 비디오 컨테이너의 자막 트랙(`mov_text`, `srt`, `vtt`) 또는 `.srt` 파일을 감지하여 참석자 명단 및 안건 참조로 활용합니다.
   - **Stage 0 (전문 용어집 및 언어 코드 감지)**: 도메인 용어집을 구축하고 주요 음성 `BCP-47` 언어 코드(예: `ko-KR`, `cmn-Hant-TW`, `en-US`, `ja-JP`)를 감지합니다.
   - **Stage 1 (음향 기준 ASR 전사)**: 16 kHz 모노 오디오를 추출하고 **Gemini 3.5 Transcribe**(기본값) 또는 **로컬 Whisper + Sherpa-ONNX**(오프라인 모드 지정 시)를 통해 물리적 타임스탬프 `[MM:SS - MM:SS]`와 화자 구간을 고정합니다.
   - **Stage 2 (멀티모달 비전 융합 및 청크 교정)**: 대용량 비디오(>250 MB)를 Apple Silicon `VideoToolbox` 하드웨어 가속으로 720p H.264(`10 fps`, `1초 GOP -g 10`, `+faststart`, `libx264` 자동 폴백 지원)로 압축하여 클라우드 업로드 및 Agentic 프레임 탐색을 가속화한 뒤, Stage 1 녹취록과 함께 **Gemini 3.8 Flash**에 전달합니다. 슬라이드와 명패를 읽어 섹션 1–5 요약을 생성하고, 섹션 6 전문 녹취록을 60줄 단위 병렬 배치로 표기법 및 용어 교정을 수행합니다(원본 타임스탬프는 엄격히 유지됨).

3. **순수 오디오 고정밀 파이프라인 (Voice Recorders & Podcasts)**:
   - **Stage 0 (전문 용어집 및 언어 코드 감지)**: 오디오에서 전문 용어와 주요 발화 언어 코드를 추출합니다.
   - **Stage 1 (음향 음성 전사)**: **Gemini 3.5 Transcribe**(또는 오프라인 Whisper + Sherpa-ONNX)로 타임스탬프가 포함된 발화 기록을 생성합니다.
   - **Stage 2 (핵심 요약 및 표기 교정)**: **Gemini 3.8 Flash**를 사용하여 임원 요약과 실행 항목을 생성하고 섹션 6 전문 녹취록의 표기를 교정합니다.

---

## 듀얼 엔진 아키텍처 (Dual-Engine Architecture)

### 1. 클라우드 Vertex AI 모드 (기본값)
* **모델 연동**: 음향 전사에는 **Gemini 3.5 Transcribe**(`TRANSCRIBE_MODEL`), 멀티모달 분석 및 교정에는 **Gemini 3.8 Flash**(`SUMMARY_MODEL`)를 사용합니다.
* **적응형 오디오 전처리**: 25분을 초과하는 장시간 오디오는 무음 구간을 기준으로 20분 청크로 분할하여 병렬 전사합니다.
* **결정론적 접두사 잠금**: 섹션 6의 모든 교정된 발화에 Stage 1의 `[MM:SS - MM:SS] **spk_X**:` 접두사를 재결합하여 타임스탬프 오차나 출력 잘림을 방지합니다.
* **GCS 2단계 수명 주기 관리**: `gs://<bucket>/raw/`에 스테이징된 원본 미디어는 **2일 후** 자동 삭제되며, 최종 결과물은 **15일간** 보관됩니다.

### 2. 로컬 오프라인 모드 (명시적 요청 전용)
* **명시적 활성화**: 사용자가 대화에서 "로컬/오프라인 전사"를 명시적으로 요청한 경우에만 실행되며, 클라우드 오류 시 임의로 로컬 모드로 전환하지 않습니다.
* **하드웨어 가속**: Apple Silicon GPU(`mlx-whisper`) 및 CPU/CUDA(`faster-whisper`)를 지원합니다.
* **음향 성문 클러스터링**: **Sherpa-ONNX**(`eres2net` / `cam++`)로 화자 임베딩을 추출하고 단어 타임스탬프와 화자 구간을 정렬합니다.

---

## 설치 및 배포 (Installation & Deployment)

| 방법 | 대상 환경 | 설정 도구 | 주요 인터페이스 |
| :--- | :--- | :--- | :--- |
| **방법 1: Google Antigravity & Agent Plugins** | 로컬 Antigravity IDE, Agent Skill | `pip` / `uv` + `./setup.sh` | Antigravity IDE 채팅 |
| **방법 2: Gemini Enterprise** | Cloud Vertex AI Agent Runtime | `./deploy.sh` (네이티브 `gcloud`) | Gemini Enterprise Web UI, Agent Engine, A2A |

### 시스템 사전 요구 사항

1. **FFmpeg 설치**:
   - **macOS**: `brew install ffmpeg`
   - **Ubuntu / Debian**: `sudo apt update && sudo apt install ffmpeg`
   - **Windows**: `winget install Gyan.FFmpeg`

2. **Google Cloud ADC 인증**:
   ```bash
   gcloud auth application-default login
   ```

### 방법 1: Google Antigravity Plugin / Skill 설정

1. **Agent Plugin으로 클론 (권장)**:
   ```bash
   git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
   ```
   - **기존 단일 Skill 디렉터리 설치 (`~/.gemini/config/skills/` 호환 방식)**:
     ```bash
     git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/plugins/meeting-transcribe-agent
     ln -s ~/.gemini/config/plugins/meeting-transcribe-agent/skills/meeting-transcribe-agent ~/.gemini/config/skills/meeting-transcribe-agent
     ```
2. **Python 의존성 패키지 설치**:
   ```bash
   pip install google-genai google-cloud-storage requests
   ```
3. **Google Cloud 환경 초기화 (`./setup.sh`)**:
   ```bash
   cd ~/.gemini/config/plugins/meeting-transcribe-agent
   chmod +x setup.sh
   ./setup.sh --project YOUR_GCP_PROJECT_ID
   ```

### 방법 2: Gemini Enterprise 클라우드 배포 (`./deploy.sh`)

```bash
uv tool install google-agents-cli
chmod +x setup.sh deploy.sh
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1
```

### 디렉터리 구조 (Agent Plugins 1.0 표준)
- **SSOT 실제 디렉터리**: `skills/meeting-transcribe-agent/`(에이전트 전용 CLI 레퍼런스가 포함된 `SKILL.md`, `scripts/`, `assets/` 포함)를 단일 진실 공급원(SSOT)으로 사용합니다.
- **2계층 `AGENTS.md` 구성**: 루트 `AGENTS.md`는 워크스페이스 및 개발 표준(Part I & Part II)을 정의하고, 플러그인 내부의 `rules/AGENTS.md`는 AI 클라이언트 실행 규칙(읽기 전용, Fail-Fast)을 정의합니다.

---

## Antigravity 조작 방법 및 사용 시나리오 (Usage & Scenarios)

Antigravity에서는 다음 두 가지 방식으로 **Meeting Transcribe Agent**를 사용할 수 있습니다:

1. **간결한 명령어 입력 (`/` 스킬 선택 + `@` 파일 태그, 권장)**: `/meeting-transcribe-agent`를 입력하여 플러그인을 선택하고 `@`로 파일을 태그합니다. `파일: @XX, 안건: @YY`처럼 핵심 항목만 지정하면 긴 문장을 작성할 필요가 없습니다.
2. **자연어 프롬프트 (자동 라우팅)**: 일상적인 대화체로 전사 및 회의록 작성을 요청하면 Antigravity가 의도를 파악하여 자동으로 플러그인을 실행합니다.

### 시나리오 1: 오디오 회의 녹음 전사 및 구조화 회의록 생성
- **간결한 `/ + @` 명령어**:
  ```text
  /meeting-transcribe-agent 파일: @meeting_recording.mp3, 주제: Executive_Board_Meeting, 참석자: John Doe, Jane Smith
  ```
- **자연어 프롬프트**:
  ```text
  @meeting_recording.mp3를 전사하여 주제 "Executive_Board_Meeting", 참석자 John Doe와 Jane Smith로 회의록과 인터랙티브 플레이어를 생성해 주세요.
  ```

### 시나리오 2: 로컬 비디오 또는 YouTube 발표 전사 (슬라이드 및 명패 OCR)
- **간결한 `/ + @` 명령어**:
  ```text
  /meeting-transcribe-agent 비디오: @conference_video.mp4, 언어: 한국어
  ```
  *(YouTube 링크의 경우: `/meeting-transcribe-agent 링크: https://www.youtube.com/watch?v=VIDEO_ID, 언어: 한국어`)*
- **자연어 프롬프트**:
  ```text
  @conference_video.mp4의 화면 슬라이드와 발표 내용을 분석하여 한국어 회의록과 인터랙티브 플레이어를 만들어 주세요.
  ```

### 시나리오 3: 회의 안건 및 참고 문서를 활용한 전문 용어/직함 교정
- **간결한 `/ + @` 명령어**:
  ```text
  /meeting-transcribe-agent 파일: @meeting_recording.mp3, 안건: @agenda.md
  ```
- **자연어 프롬프트**:
  ```text
  @agenda.md의 안건과 참석자 명단을 참고하여 @meeting_recording.mp3를 회의록과 녹취록으로 만들어 주세요.
  ```

### 시나리오 4: 다국어 회의 요약 (섹션 6 원어 유지, 섹션 1–5 대상 언어 지정)
- **간결한 `/ + @` 명령어**:
  ```text
  /meeting-transcribe-agent 파일: @meeting_recording.mp3, 요약언어: 한국어
  ```
- **자연어 프롬프트**:
  ```text
  @meeting_recording.mp3를 전사해 주세요. 섹션 6은 원래 발화 언어를 유지하고, 섹션 1부터 5까지의 요약과 실행 항목은 한국어로 작성해 주세요.
  ```

### 시나리오 5: 기밀 회의 로컬 오프라인 모드 전사
- **간결한 `/ + @` 명령어**:
  ```text
  /meeting-transcribe-agent 파일: @meeting_recording.mp3, 모드: 로컬오프라인, 화자수: 4
  ```
- **자연어 프롬프트**:
  ```text
  @meeting_recording.mp3를 로컬 오프라인 모드(화자 4명)로 전사해 주세요.
  ```

---

## Google Drive 연동 및 GCS 수명 주기 정책

| GCS 경로 접두사 (`matchesPrefix`) | 저장 객체 | 보관 기간 (`age`) | 목적 |
| :--- | :--- | :--- | :--- |
| **`raw/`** | 스테이징된 오디오 청크 및 720p 비디오 (`raw/<filename>`) | **2일 (`age: 2`)** | 재실행 시 캐시 재사용을 위해 보관하며 2일 후 자동 삭제합니다. |
| **`minutes/`**, **`players/`**, **`output/`**, **`deliverables/`** | Markdown 회의록 (`.md`) 및 HTML 플레이어 (`.html`) | **15일 (`age: 15`)** | 팀 검토를 위해 15일간 보관한 후 자동 삭제합니다. |

---

## 라이선스 (License)

이 프로젝트는 [MIT License](LICENSE)에 따라 라이선스가 부여됩니다.
