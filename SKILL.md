---
name: meeting-transcribe-agent
description: Universal meeting intelligence and interactive verbatim transcription suite adhering to Agent Skills Specification. Features native Multimodal Video Pipeline (YouTube URLs & local video files with visual slide/speaker OCR and optional Agentic Video Understanding), Google Gemini 3.5 Transcribe (cloud primary audio via Vertex AI with ephemeral Cloud Storage auto-cleanup), and local Apple Silicon MLX/Whisper + Sherpa-ONNX diarization (explicit user-requested offline mode) with Gemini 3.8 Flash minutes structuring, canonical speaker consolidation, and standalone zero-dependency interactive HTML playback player.
metadata:
  version: "2.5.1"
  author: "sylphlin"
  repository: "https://github.com/sylphlin/meeting-transcribe-agent"
  category: "audio-transcription"
  specification: "https://agentskills.io/specification"
---

# Meeting Transcribe Agent

Universal meeting intelligence and interactive transcription suite adhering to the open [Agent Skills Specification](https://agentskills.io/specification).

Provides specialized pipelines tailored to input media:
1. **YouTube Multimodal Pipeline (Direct Cloud Ingestion)**: End-to-end cloud-native analysis via **Gemini 3.8 Flash** with visual lower-third caption OCR, presentation slide extraction, and optional **Agentic Video Understanding** (`--agentic`). Automatically synchronizes with an embedded 3-pane interactive video player (top-left YouTube player, bottom-left executive summary, right synchronized transcript).
2. **Local Video Two-Stage Fusion Pipeline (Audio Extraction + Multimodal Vision Fusion)**: Solves the acoustic clock drift and token limit compression inherent in single-pass LLM video analysis.
   - **Stage 1 (Acoustic Ground Truth ASR)**: Extracts 16kHz mono audio and runs speech transcription via **Google Gemini 3.5 Transcribe** (Cloud Primary) or **Local Apple Silicon MLX/Whisper + Sherpa-ONNX Diarization** (Offline Mode) to establish millisecond-accurate physical timestamps `[MM:SS - MM:SS]` and speaker turns.
   - **Stage 2 (Multimodal Vision & Minutes Fusion)**: Ingests the 720p video file alongside the Stage 1 transcript into **Gemini 3.8 Flash**. The model reads visual presentation slides, architecture diagrams, and speaker nameplates to map real identities (`Speaker 1` -> Real Name/Title) and synthesize Executive Sections 1–5.
   - **Deterministic Assembly**: Python code deterministically combines Sections 1–5 with the verbatim Section 6, applying visual speaker mappings while strictly preserving physical timestamps with 0 drift, delivering a 3-pane HTML5 video player.
3. **Pure Audio Pipeline (Audio Files & Podcasts)**: Combines **Google Gemini 3.5 Transcribe** (Cloud Primary via Vertex AI with ephemeral Cloud Storage auto-cleanup) or **Local Apple Silicon MLX / Faster-Whisper + Sherpa-ONNX Diarization** (Explicit User-Requested Offline Mode) with **Gemini 3.8 Flash** for executive minutes structuring, speaker role arbitration, and technical glossary consistency. Outputs a zero-dependency 2-pane audio player functioning 100% offline via local `file://` protocol.

---

## Directory Structure

This skill strictly complies with the [Agent Skills Specification](https://agentskills.io/specification):

```text
meeting-transcribe-agent/
├── SKILL.md                          # Skill definition and agent reference manual
├── meeting_transcribe.py             # Primary CLI entrypoint forwarder
├── scripts/                          # Modular core components
│   ├── __init__.py
│   ├── meeting_transcribe.py         # Master pipeline orchestrator (YouTube, Local Video Fusion, & Audio)
│   ├── audio_utils.py                # Video detection, YouTube title fetching, FFmpeg compression, audio extraction
│   ├── gcs_utils.py                  # Google Cloud Storage upload/download, signed URLs, ephemeral blob cleanup
│   ├── gemini_engine.py              # Multimodal video end-to-end, two-stage video fusion, Cloud Storage auto-cleanup
│   ├── diarization.py                # Local Sherpa-ONNX acoustic diarization & Whisper/MLX transcription
│   ├── glossary.py                   # Dual-track terminology mining (audio pre-scan + agenda outline)
│   ├── canonicalizer.py              # Acoustic cluster drift convergence & sequential turn merging
│   └── html_generator.py             # Multilingual title extractor & dedicated audio/video player renderer
└── assets/                           # Static assets and templates
    ├── audio_player_template.html    # Standalone 2-pane audio player template with bottom controller (file:// offline ready)
    ├── video_player_template.html    # Standalone 3-pane video player template (YouTube IFrame & local video)
    └── prompts/                      # Structured Markdown prompts
        ├── minutes_prompt.md         # Stage 2 minutes structuring & universal language localization prompt
        └── audio_glossary_prompt.md  # Track 1 audio pre-scan entity mining
```

---

## Key Capabilities

1. **Multimodal Video & Two-Stage Fusion Pipelines**:
   - **Direct YouTube URL Support**: Cloud-native single-request analysis of public meetings and conferences directly from YouTube URLs (`https://www.youtube.com/watch?v=...`, `youtu.be/...`, shorts, live).
   - **Local Video Two-Stage Fusion**: Decouples acoustic timekeeping from multimodal semantic reasoning. Stage 1 extracts audio and executes acoustic ASR for immutable timestamps; Stage 2 feeds video + transcript to Gemini 3.8 Flash for visual slide/speaker mapping; downstream deterministic assembly eliminates token ceiling truncation and hallucinated skips.
   - **Intelligent Title & File Naming**: Automatically queries YouTube's official oEmbed API or extracts Section 1's official meeting title to name files cleanly (e.g., `City_Council_Meeting_2026_minutes.md` and `_player.html`), eliminating raw video IDs.
   - **Visual Speaker & Slide Grounding**: Inspects lower-third title cards, nameplates, and presentation slides to accurately identify real participant names, governmental departments, and agenda slide numbers.
   - **Agentic Video Understanding (`--agentic`)**: Harnesses dynamic multi-turn frame navigation and tool-use (`types.MediaProcessing.AGENTIC`) for intricate multi-hour video deep dives.

2. **Dual-Engine Audio Architecture (Cloud Primary + Explicit User-Requested Local Offline)**:
   - **Primary Engine (`--engine gemini`) [MANDATORY DEFAULT]**: Multimodal cloud transcription via `gemini-3.5-transcribe` (Vertex AI) with native speaker diarization and zero-persistence Cloud Storage auto-cleanup. Lightning-fast (30~60s for 1 hour).
   - **Offline Engine (`--engine whisper`) [EXPLICIT USER-REQUEST ONLY]**: 100% local transcription running on Apple Silicon Metal GPU (`mlx-whisper`) or CPU (`faster-whisper`), combined with Sherpa-ONNX acoustic diarization. Designed for corporate intranet, air-gapped, or network-restricted environments. **Only used when the user explicitly requests offline/local transcription or specifies `--engine whisper`. Never activated autonomously as a silent fallback.**

3. **Dual-Track Global Consistency Glossary**:
   - **Track 1 (Acoustic Discovery)**: Gemini 1M lightweight pre-scan extracts an authoritative Markdown glossary of participant names, leadership titles, organizations/teams, technical terminology, and acronyms.
   - **Track 2 (Context Ingestion)**: Ingests external agenda/meeting notices (`--outline`) to prime speech recognition and eliminate homophone errors.

4. **Universal Multi-Language Adaptation & Clean Typography**:
   - **Universal LLM Localization**: The LLM dynamically adapts all section headings, metadata field labels, and table column headers into the target user/meeting language (Traditional Chinese, English, Japanese, etc.) without hardcoded code branching.
   - **Strictly No Emojis in Headings**: Under all circumstances, section titles and table headers are output in clean, professional plain text without emojis or decorative icons (no 📌, 🎯, 💡, ⚖️, 📋, 🎙️).
   - **Verbatim Fidelity**: The Full Verbatim Transcript strictly preserves the original spoken language and words of each participant, avoiding cross-lingual translation to maintain legal and evidentiary integrity.

5. **Dedicated Dual Interactive HTML Players**:
   - **Dedicated Video Player (`video_player_template.html`)**: 3-pane layout featuring an embedded 16:9 video player on the top left (YouTube API or local `<video controls>`), an executive summary panel on the bottom left, and an interactive synchronized transcript on the right.
   - **Dedicated Audio Player (`audio_player_template.html`)**: 2-pane layout (Executive Summary on the left, synchronized transcript on the right) with a fixed floating audio control bar at the bottom. Operates 100% offline via local `file://` protocol without requiring any local HTTP server.
   - **Summary-First Copy Workflow**: Top copy buttons (`Google Docs` / `Markdown`) default to copying the Executive Summary (Sections 1–5), with dropdown menus for Full Record (Summary + Transcript) and Transcript only.
   - **Multilingual UI Switcher**: Top-right 5-language UI switcher (Traditional Chinese, English, Japanese, Korean, Simplified Chinese) with persistent localStorage preference.

---

## Standard Agent Workflow (Autonomous Pipeline Execution)

When Antigravity or any compatible agent is instructed by the user to transcribe, summarize, or analyze a meeting (audio file, video file, or YouTube URL), follow this protocol:

### Fail-Fast & Engine Selection Protocol
1. **Default to Cloud Engine (`--engine gemini`)**: Always run with the primary cloud pipeline unless the user explicitly requested local or offline transcription.
2. **Explicit Opt-in for `--engine whisper`**: The agent is STRICTLY PROHIBITED from autonomously selecting or falling back to `--engine whisper`. It MUST ONLY be activated when the user explicitly requests local/offline mode or passes `--engine whisper`.
3. **Fail-Fast on External Auth / Permission / GCS Errors**: If GCS upload or Vertex AI transcription encounters an authentication error (`401`, `RefreshError`, `invalid_scope`), permission denial (`403 Forbidden`, `AccessDeniedException`), quota exhaustion, or bucket missing error:
   - **HALT EXECUTION IMMEDIATELY**.
   - **DO NOT** attempt blind speculative retries or probing alternative buckets.
   - **DO NOT** silently switch to `--engine whisper`, extract container subtitles, or run local OCR workarounds.
   - Report the exact blocked error to the user with actionable remediation steps (e.g., `gcloud auth application-default login`, granting `roles/storage.objectUser`, or providing a bucket with `--bucket`) and wait for user direction.

### Branch A: Video Pipelines (YouTube or Local Video)
For YouTube links (`https://www.youtube.com/...`) or local video files (`.mp4`, `.mov`, `.mkv`), the agent runs the appropriate video pipeline:
```bash
# YouTube Meeting (Direct cloud ingestion + YouTube player sync)
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID"

# YouTube Meeting with Agentic Video Understanding (Deep multi-turn frame exploration)
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID" --agentic

# Local Video File (Two-Stage Audio Extraction + Multimodal Vision Fusion)
python3 meeting_transcribe.py "path/to/video.mp4"
```
- For YouTube: direct cloud ingestion via `gemini-3.8-flash` delivers complete minutes and transcript in ~40s.
- For local video files: Stage 1 extracts audio and executes acoustic ASR (Gemini 3.5 Transcribe or local Whisper) for physical ground-truth timestamps; Stage 2 passes 720p video + transcript to `gemini-3.8-flash` for visual slide/speaker mapping and executive minutes synthesis; downstream deterministic assembly combines them with zero timestamp drift.
- Produces `<stem>_minutes.md` and `<stem>_player.html` (with embedded YouTube Dock or local video player).

### Branch B: Pure Audio Pipeline (Audio Files & Podcasts)
For audio recordings (`.mp3`, `.m4a`, `.wav`, `.aac`, etc.), or when video files are forced to audio via `--extract-audio`:
1. **Stage 1: Speech Transcription (Gemini 3.5 Transcribe API - Default)**:
   ```bash
   # Primary Engine: Cloud Gemini 3.5 Transcribe API (Default)
   python3 scripts/meeting_transcribe.py "path/to/audio" --only-transcript
   
   # Explicit Offline Engine: Local Whisper (ONLY when explicitly requested by user)
   python3 scripts/meeting_transcribe.py "path/to/audio" --engine whisper --only-transcript
   ```
2. **Stage 2: Agent-Native Semantic Intelligence & Minutes Structuring**:
   - The Agent synthesizes the 5 authoritative sections (Meeting Info, Executive Summary, Key Topics, Decisions & Directives, Action Items table) and formats the verbatim transcript.
   - Saves `<stem>_會議記錄.md` (or `<stem>_minutes.md`).
3. **Stage 3: Interactive HTML Player Generation**:
   ```bash
   python3 scripts/html_generator.py "path/to/audio" "path/to/<stem>_會議記錄.md"
   ```

---

## Quickstart & CLI Reference

```bash
# YouTube Meeting (Fast Multimodal)
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID"

# YouTube Meeting (Agentic Video Understanding)
python3 meeting_transcribe.py "https://www.youtube.com/watch?v=VIDEO_ID" --agentic

# Audio Recording (Cloud Gemini)
python3 meeting_transcribe.py "meeting_recording.mp3"

# Explicit Local Whisper (User-Requested Offline Mode)
python3 meeting_transcribe.py "meeting_recording.mp3" --engine whisper --whisper-backend auto
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `input_source` | Path to audio/video file (mp3, m4a, wav, mp4, mov, etc.) or YouTube URL | *(Required)* |
| `-o, --output` | Path to output Markdown file | `<stem>_minutes.md` / `<stem>_會議記錄.md` |
| `--agentic` | Enable Agentic Video Understanding (dynamic frame navigation) for video/YouTube | `False` |
| `--extract-audio` | Force extracting audio track from video files and routing to pure audio pipeline | `False` |
| `--engine` | Audio transcription engine (`gemini` for default cloud, `whisper` for explicit user offline mode) | `gemini` |
| `--whisper-backend` | Offline backend (`auto`, `mlx` for Apple Silicon GPU, `faster-whisper`) | `auto` |
| `--whisper-model` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `--no-diarization` | Disable acoustic speaker diarization in offline Whisper mode | `False` |
| `--clustering-threshold`| Acoustic clustering threshold for Sherpa-ONNX | `0.68` |
| `--num-speakers` | Exact number of speakers if known, otherwise -1 for auto-detect | `-1` |
| `--embedding-type` | Sherpa-ONNX embedding architecture (`eres2net`, `cam++`) | `eres2net` |
| `--project` | Google Cloud project ID for Vertex AI | `GOOGLE_CLOUD_PROJECT`/`GCP_PROJECT` env var, or ADC default project |
| `--region` | Google Cloud region for Vertex AI | `GOOGLE_CLOUD_LOCATION` env var, or `global` |
| `--bucket` | GCS bucket used to stage local audio/video for Gemini | `MEETING_STORAGE_BUCKET` env var |
| `--transcribe-model` | Gemini cloud speech transcription model | `TRANSCRIBE_MODEL` env var, or `gemini-3.5-transcribe-preview` |
| `--summary-model` | Gemini executive summary and vision model | `SUMMARY_MODEL` env var, or `gemini-3.8-flash` |
| `--outline` | Path to external meeting notice, outline, or agenda document | `None` |
| `--force-glossary` | Force re-extraction of global consistency glossary (bypassing cache) | `False` |
| `--no-glossary` | Skip global consistency glossary extraction | `False` |
| `--no-player` | Disable interactive HTML player generation | `False` |
| `--no-compress` | Do not compress audio before uploading to Gemini API | `False` |
| `--summary-language` | Target summary language (`auto` mirrors user dialogue; or `en`, `zh-TW`, `ja`, etc.) | `None` (auto) |
| `--only-transcript` | Run only Stage 1 transcription and output verbatim transcript without Stage 2 | `False` |
| `--language` | Spoken audio language code for offline Whisper ASR (`auto`, `en`, `zh`, `ja`) | `auto` |
| `--serve` | Automatically spin up local HTTP server and open browser (recommended for YouTube) | `False` |

