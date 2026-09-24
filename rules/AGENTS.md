# Meeting Transcribe Agent - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution & Direct CLI Invocation (Do Not Modify Plugin Code)
- All Python scripts (`skills/meeting-transcribe-agent/scripts/*.py`, symlinked at `scripts/*.py` and `meeting_transcribe.py`), templates (`skills/meeting-transcribe-agent/assets/`, symlinked at `assets/`), and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with `replace_file_content`, `write_to_file`, or shell commands.
- Do NOT write ad-hoc temporary Python scripts or custom transcription/summarization logic.
- Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/meeting-transcribe-agent/SKILL.md` (`../../`, e.g., `/Users/sylph/.gemini/config/plugins/meeting-transcribe-agent`).
- Set `Cwd` to `<PLUGIN_ROOT>` and run `python3 skills/meeting-transcribe-agent/scripts/meeting_transcribe.py` (or `python3 meeting_transcribe.py`) directly with `run_command` using the specified arguments. Do NOT search for global CLI aliases with `find_by_name` or `list_dir`.

## 2. Fail-Fast on Errors & Explicit Engine Selection (Do Not Debug or Rewrite Code)
- Always default to the primary Cloud Gemini engine (`--engine gemini`). Activate `--engine whisper` ONLY when the user explicitly requests local/offline transcription.
- If a script fails (exit code is not 0) or an external error occurs (such as 401 Unauthorized, 403 Forbidden, Quota Exceeded, missing Application Default Credentials, or missing FFmpeg):
  - Stop immediately.
  - Show the exact error message and exit status to the user.
  - Give a clear, actionable solution to the user (for example, run `./setup.sh`, run `gcloud auth application-default login`, or install FFmpeg).
  - Do NOT try to modify the script, probe different code paths, or silently downgrade to `--engine whisper`.

## 3. Strict Zero-Emoji Policy & Dynamic Language Mirroring
- Do NOT use emojis or decorative icons in headings or tables in generated Markdown reports.
- Keep all documentation and reports in plain, professional technical text.
- Always respond to the user in their prompt language (Traditional Chinese `zh-TW` when prompted in Traditional Chinese, English when prompted in English, Japanese when prompted in Japanese, etc.).

## 4. Verbatim Transcript Language Integrity
- Section 6 (Verbatim Transcript) MUST keep the original spoken words and language of each speaker.
- Do NOT translate Section 6.
