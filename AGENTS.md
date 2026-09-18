# Developer & Maintenance Operational Rules (AGENTS.md)

This document serves as the project memory and permanent operational guidelines for the **Meeting Transcribe Agent** codebase. All agents and developers must strictly adhere to these invariant rules across all future tasks and iterations.

---

## 1. Universal Multi-Language Support (Core Target, Zero Hardcoded Branching)

- **Universal Core Target**: The system is designed for universal multi-lingual meeting transcription and intelligence (including English, Japanese, Traditional Chinese, Simplified Chinese, German, French, Spanish, etc.).
- **Strictly Prohibited**: Never introduce language-specific hardcoded branching (e.g., `if is_chinese:`, `if lang == "zh":`) or hardcoded localized schemas/strings in Python code or prompt templates.
- **Dynamic LLM-Driven Localization**:
  - The LLM dynamically adapts and translates all Section Headings (1 to 6), metadata field labels, and table column headers into the target language specified by the user or inferred from the meeting audio/video.
  - Section 6 (Full Verbatim Transcript) always strictly preserves the original spoken language and words of each participant without translation.

---

## 2. Prompts and Python Code Strictly in ASD-STE100 English

- **ASD-STE100 Standard**: All prompt templates (`assets/prompts/*.md`), inline prompts (`scripts/gemini_engine.py`), docstrings, and code comments MUST strictly follow **ASD-STE100 (Simplified Technical English)** principles:
  - Use short, direct sentences (keep instructions below 20 words where possible).
  - Use a restricted, controlled vocabulary with clear and unambiguous meanings.
  - Use the imperative mood for instructions (e.g., "Do not change", "Verify the output", "Write clean Markdown").
  - Maintain active voice; avoid passive voice, convoluted clauses, and vague adverbs.
  - Give one instruction per sentence.
- **English for Code Base**: All Python code (`*.py`), including variable names, class/function definitions, docstrings, comments, log output, error messages, and CLI help descriptions, MUST be written in professional, concise English adhering to ASD-STE100 principles.

---

## 3. Strict Generality & Neutrality (Zero Specific Name / Scenario Hardcoding)

- **Zero Entity Hardcoding**:
  - Never introduce hardcoded logic or special branches tailored to specific individual names (e.g., specific test attendees or participants like "John Doe", "Jane Smith"), specific organizations, municipalities, company names, or ad-hoc domain jargon.
  - All speaker consolidation (canonicalization), table parsing, and entity alignments must rely on generic pattern matching, dynamic header-aware semantics, and broad heuristics that generalize across any enterprise, governmental, or academic meeting globally.
- **Completely Generic Documentation**:
  - Documentation (`README.md`, localized READMEs, `SKILL.md`), scripts (`scripts/*.py`), and prompt files must remain completely generic and production-ready.
  - NEVER include test-specific meeting names, specific test URLs, or ad-hoc local testing assets in repo files:
    - Do NOT use specific test meeting names (such as municipal city council test cases).
    - Do NOT use specific test video IDs.
    - Do NOT reference temporary test file paths or scratch artifacts.
- **Standard Placeholders Only**: Always use generic, standard placeholders:
  - YouTube URLs: `https://www.youtube.com/watch?v=VIDEO_ID`
  - Meeting titles: `Executive_Board_Meeting`, `City_Council_Session`, `Product_Roadmap_Sync`
  - Audio/Video files: `meeting_recording.mp3`, `conference_video.mp4`
  - Attendee names: `John Doe`, `Jane Smith`

---

## 4. Typography & Plain Text Formatting (Strict Zero-Emoji Policy)

- **No Emojis in Section Titles or Tables**: Under no circumstances should emojis or decorative icons (e.g., 📌, 🎯, 💡, ⚖️, 📋, 🎙️) be used in section headings (`## 1. `, `## 2. `, etc.), sub-headings, or table headers.
- **Executive Plain Text**: Maintain clean, professional, enterprise-grade Markdown typography.

---

## 5. Architectural Invariants

- **Runtime Dependencies**: The project is 100% Python + standard browser HTML/CSS/JavaScript. Never introduce Node.js or npm dependencies into the runtime or workflow.
- **Standalone Audio Offline Playback**: The generated audio HTML player must function 100% offline when opened directly via local file protocol (`file:///...`) with zero local HTTP server requirements.
- **Dedicated Player Templates**: Maintain separate dedicated templates:
  - `assets/audio_player_template.html`: 2-pane layout with bottom floating audio controller.
  - `assets/video_player_template.html`: 3-pane layout with YouTube IFrame API & native HTML5 video player.
- **Summary-First Copy**: Default copy buttons in the player must target the Executive Meeting Summary, offering dropdown options for Full Record and Verbatim Transcript.

---

## 6. Commit & Attribution Policy (No AI Attribution)

- **No AI/Assistant Branding**: Never include any AI assistant name (e.g., "Claude", "Gemini", "Copilot") in branch names, commit messages, PR titles/descriptions, code comments, or file contents.
- **No Co-Authorship Trailers**: Never append `Co-Authored-By`, session links, or any other AI-attribution trailer to commit messages or PR descriptions.
- **Human Authorship Only**: All commits must be authored as the repository owner (`sylphlin <sylph.lin@gmail.com>`), with no secondary author line.

---

## 7. Model Invariants & Single Source of Truth

- **Upstream Source of Truth**:
  - `scripts/` and `assets/` are the canonical upstream source of truth for all transcription, summarization, and UI logic.
  - `gemini-enterprise/app/core/` and `gemini-enterprise/app/assets/` are downstream mirrors required by `agents-cli` Docker scoping.
  - When reconciling drifts between `scripts/` and `gemini-enterprise/`, NEVER downgrade or overwrite upstream `scripts/` with stale downstream files. Always port upstream changes down to `gemini-enterprise/`.
- **Strict Prohibition on Using Non-Designated Models**:
  - The agent is strictly prohibited from altering, substituting, downgrading, or introducing any model identifiers outside the designated models specified in `.env` / `.env.example`.
  - Never autonomously switch or fallback to non-designated or invented model IDs.
  - When models transition in the future (e.g., from preview to GA, or to newer model generations), changes must be governed exclusively via `.env` / `.env.example` configurations or explicit user instructions, never by agent speculation.
- **Dynamic Configuration via Environment Variables**:
  - Models must always be loaded dynamically from environment variables:
    - Speech transcription model: `TRANSCRIBE_MODEL` (fallback: approved designated model from `.env.example`)
    - Executive summary / multimodal vision model: `SUMMARY_MODEL` (fallback: approved designated model from `.env.example`)
  - Never hardcode ad-hoc or unapproved model names across the codebase; configure them in `.env`.

---

## 8. Fail-Fast & Explicit Engine Selection (Strict Zero Silent Fallback)

- **Explicit Engine Selection Only**:
  - The primary engine is Cloud Gemini (`--engine gemini`).
  - The local Whisper engine (`--engine whisper`) is STRICTLY user-explicit: it MUST ONLY be activated when the user explicitly requests local/offline transcription or explicitly provides the `--engine whisper` flag.
  - The agent is STRICTLY PROHIBITED from autonomously downgrading or switching to `--engine whisper` without explicit user instruction.
- **Fail-Fast on External Infrastructure & Auth Errors**:
  - Whenever encountering external authentication (`401`, `RefreshError`), permission denials (`403 AccessDeniedException`), cloud storage, or quota errors, the agent MUST STOP IMMEDIATELY.
  - Zero tolerance on blind retries, probing alternative buckets, extracting container subtitles, or trying random command variations.
  - The agent must immediately report the blocked error and present the actionable fix to the human user, awaiting user direction.
