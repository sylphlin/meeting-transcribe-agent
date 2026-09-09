---
name: meeting-transcribe-agent
description: Universal meeting intelligence and interactive verbatim transcription suite adhering to Agent Skills Specification. Features Google Gemini 3.5 Transcribe (cloud primary with ephemeral Files API auto-cleanup) and local Apple Silicon MLX/Whisper + Sherpa-ONNX diarization (offline backup) with Gemini 3.7 Flash minutes structuring, canonical speaker consolidation, and standalone zero-dependency interactive HTML playback player.
metadata:
  version: "2.3.0"
  author: "sylphlin"
  repository: "https://github.com/sylphlin/meeting-transcribe-agent"
  category: "audio-transcription"
  specification: "https://agentskills.io/specification"
---

# Meeting Transcribe Agent

Universal meeting intelligence and interactive transcription suite adhering to the open [Agent Skills Specification](https://agentskills.io/specification).

Combines **Google Gemini 3.5 Transcribe** (Cloud Primary) with **Local Apple Silicon MLX / Faster-Whisper + Sherpa-ONNX Diarization** (Offline Backup) and **Gemini 3.7 Flash** for executive minutes structuring, speaker role arbitration, and technical glossary consistency. Delivers executive-grade meeting records, domain-agnostic participant role arbitration, and an interactive, zero-dependency HTML playback player.

---

## Directory Structure

This skill strictly complies with the [Agent Skills Specification](https://agentskills.io/specification):

```text
meeting-transcribe-agent/
├── SKILL.md                          # Skill definition and agent reference manual
├── meeting_transcribe.py             # Primary CLI entrypoint forwarder
├── scripts/                          # Modular core components
│   ├── __init__.py
│   ├── meeting_transcribe.py         # Master pipeline orchestrator (Cloud & Offline Dual-Engine)
│   ├── audio_utils.py                # Audio duration, formatting & FFmpeg pre-compression
│   ├── gemini_engine.py              # Gemini multimodal client, Files API upload & auto-cleanup
│   ├── diarization.py                # Local Sherpa-ONNX acoustic diarization & Whisper/MLX transcription
│   ├── glossary.py                   # Dual-track terminology mining (audio pre-scan + agenda outline)
│   ├── canonicalizer.py              # Acoustic cluster drift convergence & sequential turn merging
│   └── html_generator.py             # Markdown AST converter & interactive player renderer
└── assets/                           # Static assets and templates
    ├── player_template.html          # Standalone responsive HTML5/CSS/JS player template
    └── prompts/                      # Structured Markdown prompts
        ├── minutes_prompt.md         # Stage 2 minutes structuring & speaker role arbitration
        └── audio_glossary_prompt.md  # Track 1 audio pre-scan entity mining
```

---

## Key Capabilities

1. **Dual-Engine Architecture (Cloud Primary + Local Offline Backup)**:
   - **Primary Engine (`--engine gemini`)**: Multimodal cloud transcription via `gemini-3.5-transcribe` with native speaker diarization and zero-persistence Files API auto-cleanup. Lightning-fast (30~60s for 1 hour).
   - **Offline Backup Engine (`--engine whisper`)**: 100% local transcription running on Apple Silicon Metal GPU (`mlx-whisper`) or CPU (`faster-whisper`), combined with Sherpa-ONNX acoustic diarization. Designed for corporate intranet, air-gapped, or network-restricted environments.

2. **Dual-Track Global Consistency Glossary**:
   - **Track 1 (Acoustic Discovery)**: Gemini 1M lightweight pre-scan extracts an authoritative Markdown glossary of participant names, leadership titles, organizations/teams, technical terminology, and acronyms.
   - **Track 2 (Context Ingestion)**: Ingests external agenda/meeting notices (`--outline`) to prime speech recognition and eliminate homophone errors.

3. **Canonical Speaker Identity Consolidation**:
   - Automatically resolves acoustic cluster drift (e.g., consolidating fragmented clusters `spk_1, spk_3, spk_7` into a unified identity such as `VP of Engineering (Eric)`).
   - Merges fragmented sequential turns from the same speaker with temporal gaps under 2.0 seconds while preserving dialogue continuity.

4. **Dynamic Language Mirroring Policy**:
   - **User Language Mirroring**: Meeting summary, analysis sections, and section headings automatically adapt to the primary language of the user's conversation / instructions (e.g., Traditional Chinese if conversing in Traditional Chinese; English if conversing in English; Japanese if conversing in Japanese).
   - **Explicit Override**: Overridden whenever `--summary-language` is explicitly passed (e.g., `--summary-language en`).
   - **Verbatim Fidelity**: The Full Verbatim Transcript strictly preserves the original spoken language and words of each participant, avoiding cross-lingual translation to maintain legal and evidentiary integrity.

5. **Standalone Interactive HTML Player**:
   - Dual-pane layout: Executive meeting summary on the left, synchronized verbatim transcript on the right.
   - Fixed header search bar with keyword highlighting and instant navigation.
   - One-click timestamp jumping for instant audio playback.
   - Smooth auto-scrolling with dynamic speaker glow highlighting the active utterance.
   - Top-right 5-language UI switcher (Traditional Chinese, English, Japanese, Korean, Simplified Chinese) with persistent localStorage preference.
   - Left panel "Copy to Google Docs" button with rich-text HTML clipboard export and automatic `docs.new` launching.

---

## Standard Agent Workflow (Autonomous Pipeline Execution)

When Antigravity or any compatible agent is instructed by the user to transcribe, summarize, or analyze an audio meeting file, follow this standard operational protocol:

1. **Stage 1: Speech Transcription (Gemini 3.5 Transcribe API - Default)**:
   - Antigravity cannot ingest raw audio waveforms directly, so this step **calls the Gemini 3.5 Transcribe API** via the transcription tool in `--only-transcript` mode:
     ```bash
     # Primary Engine: Cloud Gemini 3.5 Transcribe API (Default)
     python3 scripts/meeting_transcribe.py "path/to/audio" --only-transcript
     
     # Fallback Engine: Offline Local Whisper (Only if requested or network-restricted)
     python3 scripts/meeting_transcribe.py "path/to/audio" --engine whisper --only-transcript
     ```
   - This uploads the audio to Gemini Files API (ephemeral, auto-deleted) and returns `<stem>_transcript.md` with precise multimodal timestamps and speaker clustering.

2. **Stage 2: Agent-Native Semantic Intelligence & Minutes Structuring**:
   - **Antigravity directly reads** `<stem>_transcript.md` and any external outline/agenda files (`--outline`).
   - Utilizing Antigravity's native reasoning engine and expansive context window, the Agent:
     - Arbitrates speaker identities and assigns titles/roles based on dialogue context.
     - Synthesizes the 5 authoritative sections:
       1. 📌 **Meeting Information**: Topic, Date, Chair, Participants & Titles.
       2. 🎯 **Executive Summary**: High-level brief of key outcomes.
       3. 💡 **Key Discussion Topics**: In-depth agenda breakdown with metrics.
       4. ⚖️ **Key Decisions & Directives**: Explicit resolutions and leadership orders.
       5. 📋 **Action Items / TODO Table**: Task, Owner, Collaborators, Deadlines.
     - Formats the consolidated verbatim transcript with speaker tags (`[MM:SS - MM:SS] **Speaker**: Text`).
   - Saves the complete structured markdown to `<stem>_會議記錄.md` (or `<stem>_minutes.md`).

3. **Stage 3: Interactive HTML Player Generation & Final Delivery**:
   - Execute the lightweight generator script to compile HTML:
     ```bash
     python3 scripts/html_generator.py "path/to/audio" "path/to/<stem>_會議記錄.md"
     ```
   - Once all stages are 100% complete and verified, Antigravity opens the player in the user's browser (e.g. via `open path/to/<stem>_player.html` on macOS) for review.

---

## Quickstart & CLI Reference

```bash
# Cloud Gemini (Default)
python3 meeting_transcribe.py "meeting_recording.mp3"

# Offline Local Whisper Fallback
python3 meeting_transcribe.py "meeting_recording.mp3" --engine whisper --whisper-backend auto
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `audio_file` | Path to audio recording (supports mp3, m4a, wav, mp4, aac, flac, etc.) | *(Required)* |
| `-o, --output` | Path to output Markdown file | `<stem>_minutes.md` / `<stem>_會議記錄.md` |
| `--engine` | Transcription engine (`gemini` for cloud, `whisper` for offline backup) | `gemini` |
| `--whisper-backend` | Offline backend (`auto`, `mlx` for Apple Silicon GPU, `faster-whisper`) | `auto` |
| `--whisper-model` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `--no-diarization` | Disable acoustic speaker diarization in offline Whisper mode | `False` |
| `--clustering-threshold`| Acoustic clustering threshold for Sherpa-ONNX | `0.68` |
| `--num-speakers` | Exact number of speakers if known, otherwise -1 for auto-detect | `-1` |
| `--embedding-type` | Sherpa-ONNX embedding architecture (`eres2net`, `pyannote`, `cam++`) | `eres2net` |
| `--api-key` | Gemini API key (reads `GEMINI_API_KEY` from environment or `~/.gemini/.env`) | `None` |
| `--transcribe-model` | Gemini cloud speech transcription model | `gemini-3.5-transcribe` |
| `--summary-model` | Gemini executive summary and minutes model | `gemini-3.7-flash` |
| `--outline` | Path to external meeting notice, outline, or agenda document | `None` |
| `--force-glossary` | Force re-extraction of global consistency glossary (bypassing cache) | `False` |
| `--no-glossary` | Skip global consistency glossary extraction | `False` |
| `--no-player` | Disable interactive HTML player generation | `False` |
| `--no-compress` | Do not compress audio before uploading to Gemini API | `False` |
| `--summary-language` | Target summary language (`auto` mirrors user dialogue; or `en`, `zh-TW`, `ja`, etc.) | `None` (auto) |
| `--only-transcript` | Run only Stage 1 transcription and output verbatim transcript without Stage 2 | `False` |
| `--language` | Spoken audio language code for offline Whisper ASR (`zh`, `en`, `ja`, `auto`) | `zh` |

