# Project Memory & Always-On Operational Rules (GEMINI.md)

This document serves as the project memory and permanent operational guidelines for the **Meeting Transcribe Agent** codebase. All agents and developers must strictly adhere to these invariant rules across all future tasks and iterations.

---

## 1. Universal Multi-Language Support (No Hardcoded Language Branching)

- **Universal Core**: This system is designed for universal multi-lingual meeting transcription and intelligence (English, Japanese, Traditional Chinese, Simplified Chinese, German, French, Spanish, etc.).
- **Strictly Prohibited**: Never introduce language-specific hardcoded branching (e.g., `if is_chinese:`, `if lang == "zh":`) or hardcoded localized schemas/strings in Python code or prompt templates.
- **Dynamic LLM-Driven Localization**:
  - The LLM dynamically adapts and translates all Section Headings (1 to 6), metadata field labels, and table column headers into the target language specified by the user or inferred from the meeting audio/video.
  - Section 6 (Full Verbatim Transcript) always strictly preserves the original spoken language and words of each participant without translation.

---

## 2. Prompts and Python Code Strictly in English

- **English for System & Engine Prompts**: All prompt templates (`assets/prompts/*.md` and inline prompts in `scripts/gemini_engine.py`) MUST be written strictly in English.
- **English for Code Base**: All Python code (`*.py`), including variable names, class/function definitions, docstrings, comments, log output, error messages, and CLI help descriptions, MUST be written in professional, concise English.

---

## 3. Strict Generality & Neutrality (Zero Test-Specific Information)

- **Completely Generic**: Documentation (`README.md`, localized READMEs, `SKILL.md`), scripts (`scripts/*.py`), and prompt files must remain completely generic and production-ready.
- **Strictly Prohibited**: NEVER include test-specific meeting names, specific test URLs, or ad-hoc local testing assets in repo files:
  - Do NOT use specific test meeting names such as "臺南市政府第 764 次市政會議" or any local municipal test cases.
  - Do NOT use specific test video IDs (e.g., `Xff98Q5bki8`).
  - Do NOT reference temporary test file paths or scratch artifacts.
- **Standard Placeholders Only**: Always use generic, standard placeholders:
  - YouTube URLs: `https://www.youtube.com/watch?v=VIDEO_ID`
  - Meeting titles: `Executive_Board_Meeting`, `City_Council_Session`, `Product_Roadmap_Sync`
  - Audio/Video files: `meeting_recording.mp3`, `conference_video.mp4`

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
