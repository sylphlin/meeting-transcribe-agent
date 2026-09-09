# Rules for Meeting Transcribe Agent

## 1. Strict 3-Stage Pipeline Discipline
When transcribing, summarizing, or analyzing meeting audio:
- **Stage 1 (ASR)**: MUST execute with `--only-transcript` to produce `<stem>_transcript.md`. Never execute full end-to-end pipeline in a single command.
- **Stage 2 (Agent-Native Structuring)**: The Agent reads `<stem>_transcript.md`, synthesizes structured minutes, resolves canonical speaker identities, and writes `<stem>_會議記錄.md` (or `<stem>_minutes.md`).
- **Stage 3 (HTML Generation)**: Run `scripts/html_generator.py "path/to/audio" "path/to/<stem>_會議記錄.md"` to generate `<stem>_player.html`.

## 2. Code Immutability Principle During Operational Tasks
- When the user's request is operational (e.g. "transcribe this audio", "generate meeting minutes"), all codebase files (`*.py`) are strictly **READ-ONLY / IMMUTABLE**.
- If a runtime error, dependency issue, or crash occurs, the Agent **MUST NOT** silently patch or edit `.py` files.
- The Agent must immediately halt execution, report the exact error traceback and diagnosed root cause to the user, and obtain explicit user approval before making any code modifications.

## 3. Decoupled Presentation Lifecycle
- Python scripts are pure CLI / data processing tools and must never invoke browser opening or create GUI side effects.
- The interactive player HTML must **ONLY** be opened by the Agent via an OS command (e.g. `open path/to/<stem>_player.html` on macOS) at the very end of the task, after all files are completely written, verified, and ready for user review.
