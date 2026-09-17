# Meeting Transcribe Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v1.0+-4285F4.svg)](https://github.com/google-gemini/generative-ai-python)
[![Gemini 3.5 Transcribe](https://img.shields.io/badge/Gemini%203.5-Transcribe-orange.svg)](https://ai.google.dev/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 프로젝트 개요 (Overview)

**Meeting Transcribe Agent**는 **Gemini 3.5 Transcribe** 및 **Gemini Agentic Video Understanding**을 기반으로 구축된 올인원 영상 및 음성 회의록 생성 AI 에이전트입니다. "멀티모달 비디오 처리 (YouTube URL 및 로컬 영상)"와 "고정밀 순수 오디오 2계층 파이프라인"을 갖추고 있으며, 공공 시정 회의, 글로벌 테크 싱크업, 심층 인터뷰 및 법적 증언 녹취 등 "밀리초 단위의 정확한 타임스탬프", "화자 분리 (Diarization)", "구조화된 회의록 요약"이 필수적인 전문 업무 환경을 위해 설계되었습니다.

### 미디어 유형별 듀얼 트랙 파이프라인：

1. **YouTube 멀티모달 클라우드 파이프라인 (YouTube 클라우드 네이티브 분석)**:
   - **단일 Request 극상의 효율**: **Gemini 3.8 Flash**를 통해 엔드투엔드 시각-음성 멀티모달 분석을 직접 수행합니다. 발표 슬라이드(Slide OCR), 발표자 명패 및 자막을 동시에 인식하여 화자의 이름과 직함을 100% 식별하며, 토큰 소모와 대기 시간을 50% 절감합니다 (32분 영상 기준 약 44초 소요).
   - **Agentic Video Understanding (`--agentic`)**: 동적 프레임 탐색 및 도구 호출을 지원하여, 수 시간 분량의 장시간 영상이나 복잡한 아키텍처 슬라이드를 심층 분석합니다.
   - **PIP(화면 속 화면) 지원 YouTube 플레이어**: 생성된 단일 독립형 HTML 플레이어에 YouTube IFrame 컨트롤러를 내장하여 타임스탬프 클릭 이동 및 노래방 스타일 실시간 하이라이트를 지원합니다.

2. **로컬 비디오 2단계 융합 파이프라인 (오디오 추출 + 멀티모달 시각 융합)**:
   - **1단계 (전용 음향 ASR 불변 타임스탬프)**: 16kHz mono 오디오를 추출하여, **Google Gemini 3.5 Transcribe** (클라우드 기본) 또는 **Apple Silicon MLX/Whisper + Sherpa-ONNX Diarization** (로컬 오프라인)으로 밀리초 단위 물리적 타임스탬프 `[MM:SS - MM:SS]`와 화자 발언 구간을 확정합니다.
   - **2단계 (멀티모달 시각 및 회의록 융합)**: 720p 영상과 1단계 전사본을 **Gemini 3.8 Flash**에 입력하여 화면 슬라이드, 아키텍처 다이어그램, 명패를 확인하고 `Speaker 1`을 실제 참석자 이름으로 매핑하며 제1~5절 핵심 회의록을 생성합니다.
   - **결정론적 결합 (Deterministic Assembly)**: Python 코드가 시각적 화자 매핑을 제6절 전사본에 적용하여 물리적 타임스탬프를 100% 보존하며, 순수 LLM 비디오 분석에서 발생하는 시간 드리프트와 텍스트 잘림을 완전히 해소합니다.

3. **순수 오디오 고정밀 2계층 파이프라인 (녹음기 / 팟캐스트 / 음성 파일)**:
   - **하위 음향 전사 (Gemini 3.5 Transcribe)**: 밀리초 단위의 단어 수준 타임스탬프(Word Timestamps)와 물리 음향 기반 화자 분리(Diarization)를 수행하여 음성 누락을 완벽히 차단합니다.
   - **상위 의미론적 재구성 (Gemini 3.8 Flash)**: 문맥 이해를 바탕으로 동음이의어 및 전문 용어를 교정하고 화자명을 정규화하여 구조화된 결정 사항과 액션 아이템을 도출합니다.
   - **로컬 오프라인 백업**: Apple Silicon GPU (MLX) / faster-whisper와 Sherpa-ONNX 음향 성문 클러스터링을 통한 로컬 실행을 지원합니다.

---

### 왜 아키텍처에서 "YouTube", "로컬 영상 파일", "순수 오디오"를 명확히 구분하는가?

실제 회의 전사 환경에서 각 매체별 인프라와 정보 밀도는 근본적인 차이가 있습니다:

1. **YouTube (클라우드 네이티브, 사전 인덱싱된 음향 클록)**:
   - Google 클라우드 백본에 YouTube ASR 자동 자막과 시간 인덱스가 구축되어 있어, Gemini 3.8 Flash의 직접 분석으로 다운로드 없이 초고속 분석과 정확한 타임스탬프를 달성합니다.
2. **로컬 영상 파일 (2단계 융합, 100% 음향-화면 동기화 및 슬라이드 독해)**:
   - 로컬 영상은 사전 구축된 음향 클록이 없으므로, LLM 단독 분석 시 심각한 시간 드리프트와 출력 길이 제한이 발생합니다. 전용 ASR로 불변의 타임스탬프를 확보하고 시각 모델로 화면 정보를 분석하는 2단계 융합이 최적의 해법입니다.
3. **순수 오디오 모드 (음향 물리 분리 및 토큰 비용 최적화)**:
   - 녹음기나 팟캐스트는 영상이 없습니다. 전용 음향 모델(Gemini 3.5 Transcribe / 오프라인 Whisper)이 초당 약 32 토큰의 최소 비용으로 정밀한 타임스탬프와 화자 분리를 제공합니다.

---

### 토큰 소비량 추정치 (경험적 벤치마크)

> [!NOTE]
> 실제 토큰 소비량은 발언 밀도, 화면 움직임, 슬라이드 복잡도에 따라 달라집니다. 아래 배율은 장시간 회의 실측에 기반한 아키텍처 참고용 추정치입니다:

* **1. 순수 오디오 2계층 파이프라인 (Pure Audio Pipeline) — `~1x` 기준 소비량**:
  - **작동 원리 및 소비량**: 음성은 초당 약 32 토큰을 소모합니다 (1시간 기준 수만~십수만 토큰 수준).
  - **포지셔닝**: **가장 경제적이며 토큰을 극대화하여 절약**. 영상이 필요 없는 녹음기, 팟캐스트, 인터뷰 등에 특화되어 전용 음향 모델이 단어 타임스탬프와 화자 분리를 엄격하게 처리합니다.
* **2. Gemini Agentic Video Understanding — `~2x` 소비량**:
  - **작동 원리 및 소비량**: Google의 최신 [Gemini Agentic Video](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-agentic-video-in-gemini/) 기술을 적용했습니다. 소비량은 순수 오디오 기준 **약 2배 수준**입니다.
  - **포지셔닝**: **본 프로젝트 권장 영상 심층 이해 모드 (`--agentic`)**. 모든 프레임을 무차별 스캔하는 대신 씽킹 캐시(Thinking Cache)와 동적 도구 호출을 결합하여 필요한 순간에만 고해상도 프레임을 탐색합니다. 수 시간 길이의 영상이나 복잡한 슬라이드 분석에 최적화되어 있습니다.
* **3. Traditional Gemini Video Understanding — `~3x` 소비량**:
  - **작동 원리 및 소비량**: 기존 영상 멀티모달은 초당 1프레임(1 FPS Uniform Sampling)을 고정 샘플링하므로 토큰 소모량이 가장 큽니다 (순수 오디오 대비 **약 3배 이상**).
  - **포지셔닝**: **【본 프로젝트 미적용, 비교 참조용】**. 고정 주기 샘플링은 정지 화면이나 불필요한 프레임을 과도하게 전송하여 토큰과 대기 시간을 낭비합니다. 본 프로젝트는 YouTube 직접 스트리밍과 Agentic 탐색을 통해 이 기존 방식을 완전히 대체했습니다.

---

## 핵심 기능 및 적용 시나리오

### 주요 기능
- **YouTube 링크 및 비디오 파일 직접 지원**: URL 또는 영상 파일 경로를 입력하면 구조화된 회의록과 대화형 플레이어를 원클릭으로 생성합니다.
- **시각적 명패 및 슬라이드 OCR 인식**: 영상 화면의 자막, 명패, 발표 자료를 분석하여 발언자의 실명과 직책을 자동 매핑합니다.
- **정밀한 화자 분리 및 타임스탬프 전사**: 각 참석자의 발언 구간과 발언 내용을 명확하게 정리합니다.
- **문맥 이해 및 자연스러운 문장 다듬기**: LLM의 문맥 이해를 통해 전문 용어와 동음이의어를 교정하고 매끄러운 텍스트를 완성합니다.
- **임원급 구조화 회의록**: 회의 개요, 핵심 요약, 주요 결정 사항, 안건별 논의 분석 및 액션 아이템(ToDo)을 제공합니다.
- **외부 의존성 제로 대화형 HTML 플레이어**: 클릭 이동, 화자별 색상 구분, 실시간 검색, 다국어 UI 전환이 가능한 단일 HTML 파일을 생성합니다.

### 추천 활용 시나리오
1. **지자체 및 공공기관 공개 회의**: YouTube 라이브 스트리밍 영상을 다운로드 없이 바로 입력하여 단체장 및 간부들의 명패를 인식하고 회의록 작성.
2. **기술 세미나 및 제품 발표회**: 발표 자료와 음성을 통합하여 핵심 기술 요약 도출.
3. **다자간 대면 회의 녹음**: 다수의 참석자가 번갈아 발언하는 장시간 회의에서도 음향 성문 드리프트를 억제하여 기록.
4. **로컬 전사 백업 요구 환경**: 네트워크가 제한된 환경에서 오프라인 Whisper + Sherpa-ONNX를 활용하여 로컬 전사 수행.

---

## 듀얼 엔진 아키텍처

### 1. 클라우드 고속 모드 (기본)
* **듀얼 모델 협업**: **Google Gemini 3.5 Transcribe** (음향 전사 및 화자 분리) + **Gemini 3.8 Flash** (구조화 및 회의록 작성).
* **스마트 오디오 전처리 (Smart Ingestion)**: 비트레이트를 자동 감지하여 저비트레이트는 원본 전송, 고비트레이트는 16kHz mono로 최적 압축.
* **듀얼 트랙 비동기 병렬 처리 (화자 식별 통일)**: 먼저 하나의 권위 있는 화자 매핑 표를 확정한 뒤, "요약/결정사항"과 "장문 전사 교정"을 병렬로 분리 생성 — 두 트랙이 동일한 화자 식별을 공유하므로 표기가 어긋나지 않으며, 응답 대기 시간도 단축.
* **데이터 잔여물 제로 (Zero-Retention)**: Google Cloud Storage에 임시 업로드된 데이터는 처리가 끝나면 `finally` 블록에서 자동 영구 삭제.

### 2. 로컬 음성 전사 백업 모드 (Whisper + Sherpa-ONNX)
* **로컬 음향 전사 백업**: 클라우드 연결이 불가능하거나 로컬 처리가 필요할 때 1단계 전사 및 화자 분리를 로컬에서 수행.
* **하드웨어 가속**: Apple Silicon GPU (`mlx-whisper`) 또는 CPU/CUDA (`faster-whisper`) 가속 지원.
* **성문 벡터 클러스터링**: **Sherpa-ONNX (3D-Speaker / PyAnnote)**를 통합하여 로컬 성문 추출 수행.
* **슬라이딩 윈도우 정렬**: 선형 듀얼 포인터 스캔을 통해 단어 타임스탬프와 성문 구간을 밀리초 단위로 정합.

---

## 에이전트 대화 프롬프트 가이드

본 프로젝트는 **AI Agent Skill**로 사용하는 것을 권장합니다. 복잡한 명령어를 외울 필요 없이 채팅창에서 에이전트에게 자연어로 요청하면 됩니다:

### 추천 프롬프트 예시:

1. **YouTube 영상 회의 전사 (명패 및 슬라이드 시각 인식)**:
   > "YouTube의 이 시정 회의 `https://www.youtube.com/watch?v=VIDEO_ID`를 전사해줘. 화면 속 명패와 슬라이드를 참조하여 구조화된 회의록과 대화형 플레이어를 만들어줘."

2. **YouTube 장시간 회의 심층 분석 (Agentic Video Understanding)**:
   > "3시간짜리 YouTube 세미나 `https://www.youtube.com/watch?v=...`야. Agentic Video 모드를 사용해서 주요 발표 슬라이드를 탐색하고 핵심 결론을 정리해줘."

3. **로컬 영상 파일 처리 (슬라이드 내용 추출)**:
   > "회의 영상 `tech_summit.mp4`를 전사해줘. 화면의 프레젠테이션 자료를 확인해서 전문 용어와 화자 이름을 정확히 반영해줘."

4. **영상에서 음성만 추출하여 처리 (토큰 절약)**:
   > "이 영상 `interview.mp4`는 고정 앵글이라 화면 정보가 중요하지 않아. 음성만 추출해서 순수 오디오 파이프라인으로 돌려줘."

5. **표준 오디오 회의 전사 (클라우드 고속 처리)**:
   > "회의 녹음 파일 `meeting.mp3`를 전사하고 핵심 요약, 결정 사항, 플레이어를 만들어줘."

6. **회의 안건/식순 사전 제공 (추천: 이름 및 용어 정확도 극대화)**:
   > "기술 회의 녹음 `backend_sync.m4a`와 안건 파일 `agenda.md`야. 참석자 직함과 기술 용어를 대조하면서 전사해줘."

7. **회의록 출력 언어 지정 (다국적 팀 지원)**:
   > "Please transcribe `executive_call.mp3`. Keep the verbatim transcript in original languages, but generate the executive summary and action items in Korean."

---

## 파이프라인 아키텍처

```mermaid
flowchart TD
    classDef inputStyle fill:#2D3748,stroke:#4A5568,stroke-width:2px,color:#fff;
    classDef routerStyle fill:#D69E2E,stroke:#B7791F,stroke-width:2px,color:#fff;
    classDef videoStyle fill:#2B6CB0,stroke:#2C5282,stroke-width:2px,color:#fff;
    classDef fusionStyle fill:#4C51BF,stroke:#3C366B,stroke-width:2px,color:#fff;
    classDef audioStyle fill:#2C7A7B,stroke:#234E52,stroke-width:2px,color:#fff;
    classDef outputStyle fill:#276749,stroke:#1C4532,stroke-width:2px,color:#fff;

    subgraph Input["📥 다원 미디어 입력 (Multi-Source Input)"]
        Y["YouTube URL (Watch / Shorts / Live)"]:::inputStyle
        V["로컬 영상 파일 (.mp4 / .mov / .mkv)"]:::inputStyle
        A["순수 오디오 파일 (.mp3 / .m4a / .wav / .aac)"]:::inputStyle
        O["회의 안건 자료 (선택 --outline)"]:::inputStyle
    end

    Router{"미디어 분기 라우터<br>(Smart Router)"}:::routerStyle

    Y --> Router
    V --> Router
    A --> Router

    subgraph YouTubeTrack["🎥 파이프라인 1: YouTube 멀티모달 클라우드"]
        YMode{"모드 선택"}:::videoStyle
        Static["⚡ 정적 프레임 모드<br>• 약 44초 초고속 완료 / 인식률 100%"]:::videoStyle
        Agentic["🤖 Agentic Video (--agentic)<br>• 동적 프레임 탐색 및 심층 분석"]:::videoStyle
        GeminiFlash["Google Gemini 3.8 Flash<br>(클라우드 네이티브 멀티모달 분석)"]:::videoStyle

        YMode -- "기본" --> Static --> GeminiFlash
        YMode -- "플래그 --agentic" --> Agentic --> GeminiFlash
    end

    subgraph LocalVideoTrack["🎬 파이프라인 2: 로컬 비디오 2단계 융합 파이프라인"]
        ExtractAudio["1. 16kHz Mono 오디오 추출"]:::fusionStyle
        VideoASR{"Stage 1: 전용 음향 ASR"}:::fusionStyle
        ASR_Gemini["Gemini 3.5 Transcribe"]:::fusionStyle
        ASR_Whisper["로컬 MLX / Faster-Whisper"]:::fusionStyle
        RawTranscript["물리 음향 타임스탬프 원본 전사본<br>• [MM:SS - MM:SS] 불변 클록"]:::fusionStyle
        
        ExtractAudio --> VideoASR
        VideoASR -- "클라우드 (기본)" --> ASR_Gemini --> RawTranscript
        VideoASR -- "오프라인" --> ASR_Whisper --> RawTranscript

        Stage2["Stage 2: 멀티모달 시각 융합<br>Google Gemini 3.8 Flash<br>• 슬라이드/명패 시각 OCR<br>• Speaker 1 -> 실제 참석자 매핑<br>• 제1~5절 핵심 회의록 생성"]:::fusionStyle
        RawTranscript --> Stage2
        V -. "720p 영상 업로드" .-> Stage2

        Deterministic["결정론적 Python 결합<br>• Stage 1 물리적 타임스탬프 100% 보존<br>• 드리프트·누락·환각 제로"]:::fusionStyle
        Stage2 --> Deterministic
        RawTranscript --> Deterministic
    end

    subgraph AudioTrack["🎙️ 파이프라인 3: 순수 오디오 2계층 파이프라인"]
        Ingest["스마트 전처리<br>(비트레이트 감지 / FFmpeg 압축)"]:::audioStyle
        ASREngine{"음성 인식 엔진"}:::audioStyle
        GTranscribe["【클라우드】Gemini 3.5 Transcribe<br>• 단어 수준 타임스탬프<br>• 음향 화자 분리 (Diarization)"]:::audioStyle
        OfflineWhisper["【로컬】MLX / Faster-Whisper<br>+ Sherpa-ONNX 성문 클러스터링"]:::audioStyle
        Restructure["【문맥 구조화】Gemini 3.8 Flash<br>• 동음이의어 & 전문 용어 교정<br>• 화자 통합 & 자연스러운 문장화"]:::audioStyle

        Ingest --> ASREngine
        ASREngine -- "클라우드 (기본)" --> GTranscribe --> Restructure
        ASREngine -- "오프라인 (--engine whisper)" --> OfflineWhisper --> Restructure
        O -. 문맥 주입 .-> Ingest
    end

    Router -- "YouTube URL" --> YMode
    Router -- "로컬 영상" --> ExtractAudio
    Router -- "오디오 (또는 --extract-audio)" --> Ingest

    subgraph Delivery["📦 결과물 생성 및 플레이어"]
        MD["📄 구조화 회의록.md<br>(기본 정보 / 요약 / 결정사항 / ToDo / 전사본)"]:::outputStyle
        HTML["🌐 독립형 3분할 / 2분할 대화형 플레이어.html"]:::outputStyle
        YTDock["🎬 PIP 지원 YouTube 도크<br>(정확한 클릭 이동 & 노래방 싱크)"]:::outputStyle
        VPlayer["🎬 HTML5 비디오 플레이어<br>(음향-영상 동기화 타임스탬프 이동)"]:::outputStyle
        AudioPlayer["🎵 네이티브 오디오 플레이어<br>(진행 바 타임스탬프 이동)"]:::outputStyle

        MD --> HTML
        HTML --> YTDock
        HTML --> VPlayer
        HTML --> AudioPlayer
    end

    GeminiFlash --> MD
    GeminiFlash -. YouTube 영상 로드 .-> YTDock
    Deterministic --> MD
    Deterministic -. 로컬 영상 로드 .-> VPlayer
    Restructure --> MD
    Restructure -. 오디오 로드 .-> AudioPlayer
```

### 처리 파이프라인 단계 설명 (Pipeline Steps Explained)

#### 1단계: 입력 미디어 감지 및 스마트 라우팅
- **YouTube URL**：**🎥 파이프라인 1: YouTube 멀티모달 클라우드** 로 라우팅.
- **로컬 영상 파일**(`.mp4`, `.mov`, `.mkv`, `.webm`)：**🎬 파이프라인 2: 로컬 비디오 2단계 융합 파이프라인** 으로 라우팅.
- **오디오 파일**(`.mp3`, `.m4a`, `.wav`) 또는 `--extract-audio` 지정 시：**🎙️ 파이프라인 3: 순수 오디오 2계층 파이프라인** 으로 라우팅.

---

#### 2A단계: YouTube 멀티모달 클라우드 파이프라인
1. **클라우드 직접 스트리밍 및 다운로드 불필요**: API로 URL을 직접 전달하여 429 요청 제한을 회피합니다.
2. **단일 Request 엔드투엔드 통합 분석**:
   - `gemini-3.8-flash`가 화면 OCR(명패, 자막, 슬라이드)과 음성을 동시에 분석합니다.
   - 1회 호출로 정확한 화자명이 포함된 회의록과 타임스탬프 전사본을 한 번에 산출합니다.

---

#### 2B단계: 로컬 비디오 2단계 융합 파이프라인
1. **1단계 (전용 음향 ASR 불변 타임스탬프 확정)**:
   - 16kHz mono 오디오를 자동으로 추출합니다 (캐싱 메커니즘으로 중복 추출 방지).
   - `gemini-3.5-transcribe` (클라우드) 또는 `mlx-whisper` (로컬 오프라인)을 호출하여 밀리초 단위 물리적 타임스탬프 `[MM:SS - MM:SS]`와 화자 발언 구간을 생성합니다.
2. **2단계 (멀티모달 시각 융합)**:
   - 720p H.264로 압축하여 Cloud Storage에 임시 업로드합니다 (완료 후 즉시 자동 삭제).
   - 영상과 1단계 전사본을 `gemini-3.8-flash`에 전달하여 화면 명패 및 슬라이드 OCR을 확인하고, `Speaker 1`을 실제 참석자 이름으로 매핑하며 제1~5절 핵심 회의록을 생성합니다.
3. **결정론적 결합**:
   - Python 코드가 시각적 매핑을 물리적 전사본에 적용하여 타임스탬프 드리프트 0을 달성합니다.

---

#### 2C단계: 순수 오디오 2계층 파이프라인 (순수 오디오 녹음)
1. **스마트 전처리**: 비트레이트를 자동 감지하여 필요 시 16kHz mono로 자동 변환합니다.
2. **용어 및 식순 사전 탐색**: 회의 안건 파일(`--outline`)에서 인명과 고유 명사를 사전에 추출합니다.
3. **하위 음향 전사**: Gemini 3.5 Transcribe 또는 로컬 Whisper + Sherpa-ONNX로 단어 수준 타임스탬프와 화자 분리를 실행합니다.
4. **상위 문맥 구조화**: Gemini 3.8 Flash로 동음이의어를 교정하고 결정 사항과 액션 아이템을 정리합니다.

---

#### 3단계: 결과물 생성 (Delivery)
- **구조화된 회의록 Markdown**: `<파일명>_會議記錄.md`로 저장.
- **대화형 HTML 플레이어**: `<파일명>_player.html`로 저장 (YouTube PIP 창, 로컬 비디오 플레이어 및 오디오 컨트롤러 내장).

---

## 설치 및 배포 가이드 (Installation & Deployment)

Meeting Transcribe Agent는 두 가지 서로 다른 실행 및 설치/배포 방식을 지원합니다:

| 실행 플랫폼 | 설치/배포 방식 | 필수 환경 변수 구성 | 주요 사용자 인터페이스 |
| :--- | :--- | :--- | :--- |
| **Google Antigravity** | AI Agent Skill 형태로 작업 공간에 설치 | 프로젝트 루트 `.env` 설정 파일 | Antigravity IDE / CLI 대화창 (자연어 지시 `SKILL.md`) |
| **Gemini Enterprise** | `deploy.sh`를 통해 Vertex AI Agent Runtime으로 배포 | `deploy.sh` 인자 또는 `gemini-enterprise/.env` | Gemini Enterprise 포털 웹 UI, Vertex AI Agent Engine, A2A 프로토콜 |

---

### 기본 환경 요구사항 (Common Prerequisites)

1. **FFmpeg** (오디오/영상 포맷 탐지 및 가변 비트레이트 전처리):
   - **macOS**: `brew install ffmpeg`
   - **Ubuntu/Debian**: `sudo apt update && sudo apt install ffmpeg`
   - **Windows**: `winget install Gyan.FFmpeg`

2. **Google Cloud 인증 (ADC)**:
   Gemini API는 Vertex AI 및 Application Default Credentials (ADC)를 사용합니다:
   ```bash
   gcloud auth application-default login
   ```

---

### 방식 1: Google Antigravity 설치 (로컬 AI Agent 스킬 & CLI)

AI Agent의 로컬 스킬로 Antigravity에 설치하여 IDE 환경에서 대화형으로 회의록을 생성하거나 Python CLI로 독립 실행합니다:

1. **Skill을 Antigravity에 설치**:
   - **글로벌 스킬 (Global Skill)** (모든 작업 공간에서 사용 가능, 권장):
     ```bash
     git clone https://github.com/sylphlin/meeting-transcribe-agent.git ~/.gemini/config/skills/meeting-transcribe-agent
     ```
   - **워크스페이스 전용 스킬 (Workspace Skill)** (현재 프로젝트에서만 사용):
     ```bash
     git clone https://github.com/sylphlin/meeting-transcribe-agent.git .agent/skills/meeting-transcribe-agent
     ```

2. **Python 의존 패키지 설치**:
   ```bash
   pip install google-genai google-cloud-storage
   ```
   *(선택 사항: 오프라인 Whisper 백업 실행 시 `pip install mlx-whisper sherpa-onnx` 또는 `pip install faster-whisper sherpa-onnx`)*.

3. **환경 변수 설정 (`.env`)**:
   `.env.example`을 `.env`로 복사하고 모델 location을 `global`, 클라우드 인프라 region을 `us-central1`로 설정합니다:
   ```bash
   cp .env.example .env
   ```
   `.env` 파일 예시:
   ```bash
   GOOGLE_CLOUD_PROJECT=your-gcp-project-id
   GOOGLE_CLOUD_LOCATION=global
   GCP_REGION=us-central1
   MEETING_STORAGE_BUCKET=meeting-transcribe-your-gcp-project-id
   TRANSCRIBE_MODEL=gemini-3.5-transcribe-preview
   SUMMARY_MODEL=gemini-3.8-flash
   ```
   *(클라우드 Gemini로 로컬 파일을 처리할 경우 사전에 버킷을 생성할 수 있습니다: `gcloud storage buckets create gs://meeting-transcribe-your-gcp-project-id --location=us-central1`).*

4. **Antigravity에서 사용하기**:
   Antigravity가 `SKILL.md`를 자동으로 색인합니다. 대화창에서 자연어로 요청하기만 하면 됩니다:
   > "임원 회의 녹음 파일 `meeting.mp3`를 전사하고 핵심 요약, 결정 사항, 플레이어를 만들어줘."

---

### 방식 2: Gemini Enterprise 설치 (클라우드 Vertex AI Agent Runtime 배포)

Google ADK 2.0 및 `agents-cli`를 사용하여 Vertex AI Agent Runtime(Agent Engine / Reasoning Engine)에 엔터프라이즈 매니지드 서비스로 배포합니다.

본 배포 프로세스는 **100% 네이티브 `gcloud`** 명령어로 리소스를 프로비저닝하므로 Terraform 등 외부 도구 의존성이 없으며, Google Cloud Shell에서 바로 원클릭 배포할 수 있습니다:

1. **배포 CLI 도구 설치 (`uv` 및 `google-agents-cli`)**:
   ```bash
   uv tool install google-agents-cli
   ```

2. **`./deploy.sh`를 통한 원클릭 자동 배포**:
   내장된 배포 스크립트가 엔드투엔드 배포 라이프사이클을 전자동으로 처리합니다:
   - GCS 버킷 `gs://meeting-transcribe-${PROJECT_ID}` 생성/검증, 24시간 CORS 설정 및 자동 수명 주기 삭제 규칙 적용(`raw/` 임시 파일은 2일 후 자동 파기, 회의록 및 플레이어는 30일 보존).
   - 전용 서비스 계정 `meeting-transcribe-sa` 생성 및 최소 권한 부여 (`roles/storage.objectUser`, `roles/aiplatform.user`, `roles/logging.logWriter`).
   - `agents-cli deploy`를 통한 코드 패키징 및 Vertex AI Agent Runtime 배포.
   - 기업 Gemini Enterprise 확장에 에이전트 자동 등록 및 연결.

   ```bash
   chmod +x deploy.sh

   # 자동 배포 (.env 로드, gcloud 기반 클라우드 리소스 전자동 생성, 배포 및 Gemini Enterprise 연동 일괄 실행):
   ./deploy.sh

   # 또는 프로젝트 및 리전 지정:
   ./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1

   # 시뮬레이션 실행 (Dry-Run):
   ./deploy.sh --dry-run
   ```

3. **엔터프라이즈 연동 및 산출물 공유**:
   - **웹 인터페이스**: Gemini Enterprise 공식 포털의 에이전트 목록에서 직접 선택하여 호출할 수 있습니다.
   - **클라우드 Agent Engine**: Vertex AI Reasoning Engine API 또는 Agent-to-Agent (A2A) 프로토콜을 통해 다른 에이전트와 연동 가능합니다.
   - **안전한 산출물 공유**: 생성된 Markdown 회의록과 HTML 플레이어는 GCS에 업로드되며 **24시간 유효한 서명된 URL (Signed URLs)**이 반환되어 로그인 없이 브라우저에서 즉시 검토할 수 있습니다.

---

## 프로젝트 디렉터리 구조

```text
meeting-transcribe-agent/
├── SKILL.md                          # Agent Skill 전용 매뉴얼 및 매개변수 정의
├── README.md                         # 영문 프로젝트 개요 및 아키텍처
├── README.ko.md                      # 한국어 프로젝트 문서
├── LICENSE                           # MIT 라이선스
├── .gitignore                        # 테스트 미디어 및 로컬 캐시 제외 설정
├── .env.example                      # Antigravity Skill용 환경 변수 샘플
├── meeting_transcribe.py             # 루트 CLI 엔트리포인트
├── deploy.sh                         # 100% 네이티브 gcloud 원클릭 배포 스크립트 (Cloud Shell 지원)
├── scripts/                          # 핵심 모듈
│   ├── __init__.py
│   ├── meeting_transcribe.py         # 메인 파이프라인 컨트롤러
│   ├── audio_utils.py                # 비트레이트 탐지 및 FFmpeg 전처리
│   ├── gemini_engine.py              # Gemini 3.5 Transcribe 및 3.8 Flash 재구성
│   ├── diarization.py                # 로컬 화자 분리 (Sherpa-ONNX)
│   ├── glossary.py                   # 고유 명사 및 인명 탐색
│   ├── canonicalizer.py              # 화자명 정규화
│   └── html_generator.py             # 독립형 대화형 HTML 플레이어 생성
├── assets/                           # 템플릿 및 프롬프트
│   ├── audio_player_template.html    # 오디오 2패널 플레이어 템플릿
│   ├── video_player_template.html    # 영상 3패널 플레이어 템플릿
│   └── prompts/                      # 프롬프트 템플릿 디렉터리
└── gemini-enterprise/                # Gemini Enterprise (ADK 2.0 / Vertex AI) 배포 패키지
    ├── deploy.sh                     # 루트 deploy.sh로 전달하는 스크립트
    ├── agents-cli-manifest.yaml      # agents-cli 배포 명세서
    └── app/                          # 엔터프라이즈 에이전트 모듈
```

---

## 고급: 개발자 및 CLI 명령어 가이드 (Developer & Headless CLI)

> [!TIP]
> **일반 사용자 안내**: AI Agent(Antigravity 등)를 통해 사용할 경우 **명령어를 수동으로 입력할 필요가 없습니다**. 에이전트가 대화 맥락을 기반으로 `SKILL.md`를 읽고 최적의 매개변수를 자동 구성합니다.

### 기본 실행 (YouTube 영상)
```bash
# YouTube 영상 직접 전사 및 플레이어 생성 (초고속 멀티모달 모드)
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Agentic Video Understanding 동적 프레임 탐색 활성화
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID" --agentic
```

### 기본 실행 (오디오 및 로컬 파일)
```bash
# 클라우드 기본 모드
python3 meeting_transcribe.py "meeting_recording.mp3"

# 로컬 오프라인 백업 실행 (Apple Silicon GPU / Sherpa-ONNX)
python3 meeting_transcribe.py "meeting_recording.mp3" --engine whisper --whisper-backend auto
```

### 주요 매개변수 안내

| 매개변수 | 설명 | 기본값 |
| :--- | :--- | :--- |
| `input_source` | 오디오/영상 파일 경로 또는 YouTube URL | *(필수)* |
| `-o, --output` | 회의록 출력 Markdown 경로 | `<파일명>_minutes.md` |
| `--agentic` | 영상 Agentic Video 동적 프레임 탐색 활성화 | `False` |
| `--extract-audio` | 영상 파일에서 음원을 추출하여 오디오 파이프라인으로 강제 전환 | `False` |
| `--engine` | 오디오 전사 엔진: `gemini` (클라우드) 또는 `whisper` (로컬) | `gemini` |
| `--whisper-backend` | 오프라인 백엔드: `auto`, `mlx`, `faster-whisper` | `auto` |
| `--whisper-model` | Whisper 모델 크기 (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `--no-diarization` | 화자 분리 비활성화 | `False` |
| `--clustering-threshold` | Sherpa-ONNX 클러스터링 임계값 | `0.68` |
| `--num-speakers` | 참석자 수 (알고 있는 경우 지정, -1은 자동 감지) | `-1` |
| `--embedding-type` | Sherpa-ONNX 음향 성문 모델 (`eres2net`, `cam++`) | `eres2net` |
| `--project` | Vertex AI용 Google Cloud 프로젝트 ID | `None` (ADC/환경 변수) |
| `--region` | Vertex AI용 Google Cloud 리전 | `global` |
| `--bucket` | 로컬 오디오/영상 스테이징용 GCS 버킷 이름 | `MEETING_STORAGE_BUCKET` 환경 변수 |
| `--transcribe-model` | 클라우드 음향 음성인식 모델 (기본값: `TRANSCRIBE_MODEL`) | `gemini-3.5-transcribe-preview` |
| `--summary-model` | 구조화 회의록 및 시각 모델 (기본값: `SUMMARY_MODEL`) | `gemini-3.8-flash` |
| `--outline` | 회의 안건/식순 파일 경로 (.txt / .md) | `None` |
| `--no-player` | 대화형 HTML 플레이어 생성 비활성화 | `False` |
| `--summary-language` | 회의록 생성 언어 지정 (`auto`, `ko`, `en`, `zh-TW` 등) | `None` (auto) |
| `--serve` | 로컬 HTTP 서버를 자동 시작하고 브라우저 열기 (YouTube 영상 재생 권장) | `False` |

---

## 라이선스 (License)

이 프로젝트는 [MIT License](LICENSE)를 따릅니다.
