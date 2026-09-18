# Meeting Transcribe Agent - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution (Do Not Modify Plugin Code)
- All Python scripts (`scripts/*.py`), templates (`assets/`), and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with `replace_file_content`, `write_to_file`, or shell commands.
- Run the scripts as-is with `run_command` using the specified arguments.

## 2. Fail-Fast on Errors (Do Not Debug or Rewrite Code)
- If a script fails (exit code is not 0) or an external error occurs (such as 401 Unauthorized, 403 Forbidden, Quota Exceeded, or missing dependencies):
  - Stop immediately.
  - Show the exact error message and exit status to the user.
  - Give a clear, actionable solution to the user (for example, set the API key, install ffmpeg).
  - Do NOT try to modify the script, probe different code paths, or rewrite logic.

## 3. Strict Zero-Emoji Policy
- Do NOT use emojis or decorative icons in headings or tables in generated Markdown reports.
- Keep all documentation and reports in plain, professional technical text.

## 4. Verbatim Transcript Language Integrity
- Section 6 (Verbatim Transcript) MUST keep the original spoken words and language of each speaker.
- Do NOT translate Section 6.
