# Meeting Transcribe Agent — Workspace & Development Rules (AGENTS.md)

This file defines the authoritative rules for AI Coding Agents (Google Antigravity / Jetski, Claude Code, Codex, etc.) working in this repository. It covers both **Client Execution Invariants** (when running the meeting transcription and intelligence pipeline for end users) and **Repository Engineering Standards** (when developing, maintaining, or extending this project).

---

## Part I: Operational Invariants (When Executing Meeting Transcribe Tasks)

1. **Strict Toolset Execution Only (No Ad-Hoc Scripts)**:
   - Execute all meeting transcription, diarization, glossary extraction, minutes structuring, and interactive HTML player generation exclusively via the official scripts in `skills/meeting-transcribe-agent/scripts/` (symlinked at `scripts/` and `meeting_transcribe.py` at `<PLUGIN_ROOT>`).
   - Writing temporary Python scripts or ad-hoc transcription/summarization scripts is **STRICTLY FORBIDDEN**.
2. **Direct CLI Invocation from `<PLUGIN_ROOT>`**:
   - Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/meeting-transcribe-agent/SKILL.md` (`../../`, e.g., `/Users/sylph/.gemini/config/plugins/meeting-transcribe-agent`).
   - Set `Cwd` to `<PLUGIN_ROOT>` and run `python3 skills/meeting-transcribe-agent/scripts/meeting_transcribe.py` (or `python3 meeting_transcribe.py`) directly via `run_command`. Do NOT search for global CLI aliases with `find_by_name` or `list_dir`.
3. **Explicit Engine Selection & Fail-Fast Protocol**:
   - **Default to Cloud Engine (`--engine gemini`)**: The local Whisper engine (`--engine whisper`) is strictly user-explicit and MUST NEVER be activated autonomously as a silent fallback.
   - **Fail-Fast on External Infrastructure & Auth Errors**: Whenever encountering external authentication (`401`, `RefreshError`), permission denials (`403 AccessDeniedException`), cloud storage, or quota errors, stop immediately, report the exact error and exit status, and instruct the user to run `./setup.sh` or `gcloud auth application-default login`.
4. **Strict Zero-Emoji Policy & Verbatim Language Integrity**:
   - Do NOT use decorative emojis or icons (e.g., 📌, 🎯, 💡, ⚖️, 📋, 🎙️) in section headings (`## 1. `, `## 2. `, etc.), sub-headings, or table headers.
   - Section 6 (Full Verbatim Transcript) MUST strictly preserve the original spoken language and words of each participant without translation.

---

## Part II: Repository Development & Engineering Standards (When Developing This Project)

When modifying code, prompts, infrastructure scripts, or documentation in this repository, you MUST adhere to the following engineering standards:

### 1. Single Source of Truth (SSOT) & Symlink Integrity (Agent Plugins 1.0 Specification)
- **Canonical Code Location**: All core scripts (`scripts/*.py`), templates (`assets/*.html`), and prompt specifications (`assets/prompts/*.md`) physically reside inside `skills/meeting-transcribe-agent/scripts/` and `skills/meeting-transcribe-agent/assets/` in compliance with the [Agent Plugins 1.0 Specification](https://agent-plugins.org/specification) (§4.2 & §7.1).
- **Root Symlinks**: Top-level `SKILL.md`, `scripts`, and `assets` at the repository root are POSIX symlinks pointing to `skills/meeting-transcribe-agent/SKILL.md`, `skills/meeting-transcribe-agent/scripts`, and `skills/meeting-transcribe-agent/assets` (§4.1.3).
- **Downstream Enterprise Mirror**: `gemini-enterprise/app/core/` and `gemini-enterprise/app/assets/` are downstream mirrors required by `agents-cli` Docker scoping. Always edit files under `skills/meeting-transcribe-agent/scripts/` and `skills/meeting-transcribe-agent/assets/` first, then port upstream changes down to `gemini-enterprise/`.

### 2. Universal Multi-Language Support (Zero Hardcoded Branching)
- **Universal Core Target**: The system supports universal multi-lingual meeting transcription and intelligence (English, Japanese, Traditional Chinese, Simplified Chinese, German, French, Spanish, etc.).
- **Strictly Prohibited**: Never introduce language-specific hardcoded branching (e.g., `if is_chinese:`, `if lang == "zh":`) or hardcoded localized schemas/strings in Python code or prompt templates.
- **Dynamic LLM-Driven Localization**: The LLM dynamically adapts and translates all Section Headings (1 to 6), metadata field labels, and table column headers into the target language specified by the user or inferred from the meeting audio/video.

### 3. Prompts and Python Code Strictly in ASD-STE100 English
- **ASD-STE100 Standard**: All prompt templates (`assets/prompts/*.md`), inline prompts (`scripts/gemini_engine.py`), docstrings, and code comments MUST strictly follow **ASD-STE100 (Simplified Technical English)** principles (short, direct sentences under 20 words, active voice, imperative mood).
- **English for Code Base**: All Python code (`*.py`), variable names, class/function definitions, docstrings, comments, log output, error messages, and CLI help descriptions MUST be written in concise ASD-STE100 English.

### 4. Strict Generality & Neutrality (Zero Specific Name / Scenario Hardcoding)
- **Zero Entity Hardcoding**: Never introduce hardcoded logic or special branches tailored to specific individual names, organizations, municipalities, or company names.
- **Standard Placeholders Only**: Always use generic placeholders (`https://www.youtube.com/watch?v=VIDEO_ID`, `Executive_Board_Meeting`, `meeting_recording.mp3`, `conference_video.mp4`, `John Doe`, `Jane Smith`).

### 5. Architectural & Model Invariants
- **Runtime Dependencies**: 100% Python + standard browser HTML/CSS/JavaScript. Never introduce Node.js or npm dependencies.
- **Standalone Audio Offline Playback**: `assets/audio_player_template.html` (2-pane with bottom floating audio controller) must function 100% offline via `file:///...`. `assets/video_player_template.html` provides the 3-pane layout with YouTube IFrame API & native HTML5 video player.
- **Designated Models via `.env`**: Load models dynamically via `TRANSCRIBE_MODEL` and `SUMMARY_MODEL` from `.env` / `.env.example`. Never hardcode non-designated or invented model IDs.
- **Mandatory Unit Test Gate**: Run the complete unit test suite before committing any change:
  ```bash
  python3 -m unittest discover -s tests -v
  ```

### 6. Antigravity Plugin Architecture, 5-Language Parity, & Commit Policy
- **Plugin & README Synchronization**: Keep [plugin.json](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/plugin.json), [rules/AGENTS.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/rules/AGENTS.md), [skills/meeting-transcribe-agent/SKILL.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/skills/meeting-transcribe-agent/SKILL.md), and all 5 language READMEs ([README.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/README.md), [README.zh-TW.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/README.zh-TW.md), [README.zh-CN.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/README.zh-CN.md), [README.ja.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/README.ja.md), [README.ko.md](file:///Users/sylph/Documents/Antigravity/meeting-transcribe-agent/README.ko.md)) synchronized at all times.
- **Human Authorship Only**: Author all commits as `sylphlin <sylph.lin@gmail.com>` with zero AI assistant branding or `Co-Authored-By` trailers.
