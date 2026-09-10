"""
scripts/gemini_engine.py - Gemini Cloud Engine Client & Multimodal Orchestrator.
Handles Stage 1 Cloud ASR, Stage 2 Minutes Generation, Files API management, and Token Accounting.
"""

import os
import re
import time
from pathlib import Path
import concurrent.futures
from google import genai
from google.genai import types

from scripts.audio_utils import (
    format_offset,
    compress_audio_for_upload,
    safe_ascii_upload_path,
    should_compress_audio,
    is_youtube_url,
    is_video_file,
    optimize_video_for_upload,
)


def load_env_file():
    """Load GEMINI_API_KEY from ~/.gemini/.env or current directory .env."""
    candidates = [
        Path.home() / ".gemini" / ".env",
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
    ]
    for env_path in candidates:
        try:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if k and v and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            continue



def get_gemini_client(api_key: str = None) -> genai.Client:
    """Initialize and return a Google GenAI Client, loading key from env if needed."""
    load_env_file()
    key = api_key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise ValueError('Missing GEMINI_API_KEY. Please export it or put it in ~/.gemini/.env or .env.')
    return genai.Client(api_key=key)


def parse_transcription_parts(raw_text: str) -> str:
    """Format structured Gemini transcription parts into clean timestamped transcript."""
    lines = []
    pattern = re.compile(r'\[(\d+:\d+(?::\d+)?)\s*-\s*(\d+:\d+(?::\d+)?)\]\s*(.*)')
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            lines.append(f"[{m.group(1)} - {m.group(2)}] {m.group(3).strip()}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _parse_offset_to_seconds(val) -> float:
    """Parse Gemini timestamp offset (seconds float/int or string ending in 's') to float."""
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).rstrip("s")
    return float(s) if s else 0.0


def transcribe_with_gemini_cloud(
    client: genai.Client,
    audio_path: Path,
    model_name: str = "gemini-3.5-transcribe",
    compress: bool = True,
    language: str = "auto"
) -> tuple[str, float]:
    """Upload audio to Gemini Files API and run cloud transcription."""
    t0 = time.time()
    upload_file_path = audio_path
    temp_compressed = None

    if compress:
        needs_comp, reason = should_compress_audio(audio_path, max_size_mb=10.0, target_bitrate_kbps=48)
        if needs_comp:
            print(f"[*] Audio compression required: {reason}. Compressing to 48k...")
            temp_compressed = compress_audio_for_upload(audio_path, bitrate="48k")
            upload_file_path = temp_compressed
        else:
            print(f"[*] Skipping audio compression: {reason}.")

    uploaded_file = None
    try:
        with safe_ascii_upload_path(upload_file_path) as safe_upload_path:
            print(f"[*] Uploading audio file to Gemini Files API ({safe_upload_path.stat().st_size / (1024*1024):.1f} MB)...")
            uploaded_file = client.files.upload(file=str(safe_upload_path))
        
        poll_delays = [0.5, 1.0, 2.0]
        poll_idx = 0
        while uploaded_file.state.name == "PROCESSING":
            delay = poll_delays[min(poll_idx, len(poll_delays) - 1)]
            time.sleep(delay)
            poll_idx += 1
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            raise RuntimeError(f"Audio file upload failed: {uploaded_file.error}")

        print(f"[*] Invoking Gemini transcription model `{model_name}` for speech recognition and turn timestamps...")
        lines = []
        raw_parts = []
        has_at = False

        if "transcribe" in model_name.lower():
            at_kwargs = {
                "diarization": True,
                "word_timestamp": True,
            }
            if language and language.lower() != "auto":
                at_kwargs["language_codes"] = [language]

            config = types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(**at_kwargs)
            )
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=[uploaded_file],
                config=config
            )
        else:
            prompt = (
                "Transcribe this audio recording completely and accurately. "
                "Include precise turn timestamps in [MM:SS - MM:SS] format for every utterance. "
                "Faithfully preserve the original spoken language and words."
            )
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=[uploaded_file, prompt],
            )

        for chunk in stream:
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue
            for part in chunk.candidates[0].content.parts:
                if getattr(part, "audio_transcription", None):
                    has_at = True
                    at = part.audio_transcription
                    spk_raw = at.speaker_label or "spk:0"
                    spk_clean = spk_raw.replace(":", "_")
                    start_str = "00:00"
                    end_str = "00:00"
                    if at.words:
                        s_sec = _parse_offset_to_seconds(at.words[0].start_offset)
                        e_sec = _parse_offset_to_seconds(at.words[-1].end_offset)
                        start_str = format_offset(s_sec)
                        end_str = format_offset(e_sec)
                    text = (at.text or "").strip()
                    if text:
                        lines.append(f"[{start_str} - {end_str}] **{spk_clean}**: {text}")
                elif part.text:
                    raw_parts.append(part.text)

        if has_at:
            raw_text = "\n\n".join(lines)
        else:
            raw_text = "".join(raw_parts)

        duration = time.time() - t0
        print(f"[*] ✓ Cloud transcription complete in {duration:.1f}s ({len(raw_text)} chars).")
        return raw_text, duration

    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if temp_compressed and temp_compressed.exists():
            temp_compressed.unlink()


def generate_minutes_with_gemini(
    client: genai.Client,
    audio_path: Path,
    raw_transcript_text: str,
    global_glossary: str,
    summary_model: str = "gemini-3.8-flash",
    prompt_template_path: Path = None,
    summary_language: str = None
) -> tuple[str, float]:
    """
    Executes Stage 2: Generates complete 6-section meeting minutes and verbatim transcript.
    Uses Dual-Track Concurrency (Track A: Sections 1-5; Track B: Section 6 verbatim localization)
    to minimize wall-clock latency while preserving 100% transcript quality.
    """
    t0 = time.time()
    
    glossary_injection = f"""
=== Global Consistency Glossary ===
{global_glossary}
===================================
Strictly adhere to the spelling, names, titles, organizations, and technical terms in the glossary above.
""" if global_glossary else ""

    # Target summary language specification (universal, LLM-driven localization)
    if summary_language and summary_language.lower() != "auto":
        target_lang = summary_language
    else:
        target_lang = "the primary language spoken in the meeting (e.g. Traditional Chinese for Taiwan meetings, English for English meetings, Japanese for Japanese meetings, etc.)"

    summary_lang_instruction = (
        f"- **Sections 1 to 5 (Metadata, Summary, Discussion Topics, Decisions, Action Items)**:\n"
        f"  Must be written in fluent, native, professional {target_lang}.\n"
        f"- **Universal Language Adaptation (CRITICAL)**:\n"
        f"  1. Translate and adapt all section headings (1 to 6), metadata field labels, and table column headers naturally into {target_lang}.\n"
        f"  2. **STRICTLY FORBIDDEN to include ANY emojis or icons** (e.g. no 📌, 🎯, 💡, ⚖️, 📋, 🎙️) in any section headings, sub-headings, or table headers. Use clean, plain text markdown headings only (e.g. `## 1. `, `## 2. `)."
    )
    verbatim_lang_instruction = (
        "Faithfully preserve original spoken dialogue and language of each speaker without translation."
    )
    s1_s5_schema = f"""## 1. Meeting Metadata & Attendees
- **Meeting Title**: Inferred or established title
- **Audio Source**: `{audio_path.name}`
- **Estimated Date / Time**: Inferred from context or agenda
- **Chairperson / Host**: Identified meeting leader
- **Speaker Mapping Table**:
  Cross-reference dialogue context and acoustic turns to map every `spk_X` or `Speaker X` identifier to a real person and role:
  | Speaker ID | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- |
  | `spk_0, spk_1` | [Role/Title] | [Name or Inferred Name] | [Department / Org] |

## 2. Executive Summary
- A high-level, 200–300 word executive overview synthesizing core purpose, major themes, decisions, and outcomes.

## 3. Key Discussion Topics & Agenda Items
- Structured breakdown for each topic discussed:
  - **Context & Motivation**: Background and why this issue was raised.
  - **Key Arguments & Data**: Evidence, metrics, or points presented by participants.
  - **Discussion Flow & Speaker Perspectives**: Contributions from different leaders/members organized per speaker.
  - **Outcome / Consensus**: Conclusion reached on this specific topic.

## 4. Key Decisions & Resolutions
- Bulleted list of formal decisions, policy directives, approved motions, or consensus reached.

## 5. Action Items & Next Steps
- Structured Markdown table assigning clear ownership and timelines:
  | # | Action Item / Task | Owner / Assignee | Due Date / Timeline | Status / Notes |
  | :--- | :--- | :--- | :--- | :--- |
  | 1 | [Clear, actionable task description] | [Name / Role] | [Timeline] | [Notes] |"""

    def _call_gemini_track(prompt_content: str, label: str) -> str:
        gen_kwargs = {"model": summary_model, "contents": prompt_content}
        if hasattr(types, "ThinkingConfig"):
            try:
                gen_kwargs["config"] = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                )
            except Exception:
                pass
        print(f"[*] [Dual-Track Stage 2] Launching {label} with `{summary_model}` (thinking_budget=0)...")
        resp = client.models.generate_content(**gen_kwargs)
        return resp.text or ""

    if prompt_template_path is not None:
        template_text = Path(prompt_template_path).read_text(encoding="utf-8")
        prompt = template_text.replace("{audio_filename}", audio_path.name)
        prompt = prompt.replace("{glossary_injection}", glossary_injection)
        prompt = prompt.replace("{raw_transcript_text}", raw_transcript_text)
        prompt = prompt.replace("{summary_language_instruction}", summary_lang_instruction)
        print(f"[*] Invoking Gemini model `{summary_model}` for custom-templated meeting minutes structuring...")
        final_text = _call_gemini_track(prompt, "Custom Single-Prompt Template")
        duration = time.time() - t0
        return final_text, duration

    prompt_a = f"""# Role & Objective
You are an elite, highly professional executive meeting secretary. Your mission is to analyze the draft transcript of a recorded meeting and produce Sections 1 to 5 of an impeccably formatted, executive-ready meeting record in Markdown.

{glossary_injection}

---
# Draft Transcript
{raw_transcript_text}

---
# Language & Label Policy
{summary_lang_instruction}

---
# Output Structure (Sections 1 to 5 ONLY)
Output strictly and exclusively Sections 1 to 5 (DO NOT include any emojis or icons in headings):

{s1_s5_schema}

IMPORTANT: Stop immediately after Section 5. Do NOT output Section 6.
"""

    prompt_b = f"""# Role & Objective
You are an elite verbatim meeting transcription editor. Your mission is to transform the draft transcript into Section 6 (Full Verbatim Transcript), localized appropriately into {target_lang}.

{glossary_injection}

---
# Draft Transcript
{raw_transcript_text}

---
# Instructions
1. Map every speaker ID (`spk_X`, `spk-X`, or `Speaker X`) to their identified Role / Name based on dialogue context (e.g. Chair, Host, Presenter, Department Head, or identified Name).
2. Faithfully preserve all spoken dialogue turns, words, numbers, and chronological order without dropping or truncating sentences.
3. {verbatim_lang_instruction}
4. Format every dialogue turn strictly as:
   `[MM:SS - MM:SS] **Role / Name**: Spoken utterance`

---
# Output Format
Output strictly and exclusively Section 6 (translate heading to {target_lang}, DO NOT include any emojis or icons):
## 6. Full Verbatim Transcript

[MM:SS - MM:SS] **Role / Name**: Utterance
"""

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(_call_gemini_track, prompt_a, "Track A (Executive Synthesis & Metadata)")
            fut_b = executor.submit(_call_gemini_track, prompt_b, "Track B (Verbatim Transcript Localization)")
            
            text_a = fut_a.result()
            text_b = fut_b.result()

        final_text = f"{text_a.strip()}\n\n---\n\n{text_b.strip()}\n"
        duration = time.time() - t0
        print(f"[*] ✓ [Dual-Track Stage 2] Both tracks successfully completed in {duration:.1f}s.")
        return final_text, duration

    except Exception as e:
        print(f"[!] Dual-track concurrent generation encountered error ({e}). Falling back to single-prompt execution...")
        single_prompt = f"{prompt_a}\n\n---\n\n{prompt_b}"
        final_text = _call_gemini_track(single_prompt, "Fallback Single-Prompt")
        duration = time.time() - t0
        return final_text, duration


def process_video_meeting_end_to_end(
    client: genai.Client,
    video_source: str | Path,
    summary_model: str = "gemini-3.8-flash",
    use_agentic: bool = False,
    summary_language: str = None,
    outline_path: Path = None,
) -> tuple[str, float]:
    """
    Process a video meeting end-to-end via Gemini Multimodal Vision (YouTube URL or local video file).
    Produces complete 6 sections (Metadata & Speaker Table, Executive Summary, Topics, Decisions, Action Items, Full Verbatim Transcript)
    in a single request with optional Agentic Video Understanding.
    """
    t0 = time.time()
    source_str = str(video_source).strip()
    is_yt = is_youtube_url(source_str)

    # Outline context injection
    outline_injection = ""
    if outline_path and Path(outline_path).exists():
        try:
            outline_content = Path(outline_path).read_text(encoding="utf-8").strip()
            if outline_content:
                outline_injection = f"\n---\n# Official Meeting Outline & Agenda Reference\n{outline_content}\n---\n"
        except Exception as e:
            print(f"[!] Warning: Could not read outline file {outline_path}: {e}")

    # Target summary language specification (universal, LLM-driven localization)
    if summary_language and summary_language.lower() != "auto":
        target_lang = summary_language
    else:
        target_lang = "the primary language spoken in the meeting (e.g. Traditional Chinese for Taiwan meetings, English for English meetings, Japanese for Japanese meetings, etc.)"

    lang_instruction = (
        f"- **Sections 1 to 5 (Metadata, Summary, Discussion Topics, Decisions, Action Items)**:\n"
        f"  Must be written in fluent, native, professional {target_lang}.\n"
        f"- **Universal Language Adaptation (CRITICAL)**:\n"
        f"  1. Translate and adapt all section headings (1 to 6), metadata field labels, and table column headers naturally into {target_lang}.\n"
        f"  2. **STRICTLY FORBIDDEN to include ANY emojis or icons** (e.g. no 📌, 🎯, 💡, ⚖️, 📋, 🎙️) in any section headings, sub-headings, or table headers. Use clean, plain text markdown headings only (e.g. `## 1. `, `## 2. `).\n"
        f"- **Section 6 (Full Verbatim Transcript)**:\n"
        f"  MUST faithfully preserve original spoken dialogue and language of each speaker without translation."
    )

    video_schema = f"""## 1. Meeting Metadata & Attendees
- **Meeting Title**: (Inferred from video slides, agenda, or title)
- **Source**: {source_str}
- **Estimated Date / Time**: (Inferred from slides or dialogue)
- **Chairperson / Host**: (Identified meeting leader)
- **Speaker Mapping Table**:
  Cross-reference dialogue, nameplates, and video titles to map participants:
  | Role / Title | Name | Organization / Department | Remarks / Key Presentation Topic |
  | :--- | :--- | :--- | :--- |

## 2. Executive Summary
- A high-level, 300–400 word executive overview synthesizing core purpose, major themes, decisions, and outcomes.

## 3. Key Discussion Topics & Agenda Items
- Structured breakdown for each topic discussed: Context & Motivation, Key Arguments & Data, Discussion Flow & Speaker Perspectives (organized per speaker), Outcome.

## 4. Key Decisions & Resolutions
- Bulleted list of formal decisions, policy directives, approved motions, or consensus reached.

## 5. Action Items & Next Steps
- Structured Markdown table assigning clear ownership and timelines:
  | # | Action Item / Task | Owner / Assignee | Due Date / Timeline | Status / Notes |
  | :--- | :--- | :--- | :--- | :--- |

## 6. Full Verbatim Transcript
- Chronologically transcribe every dialogue turn.
- Follow the **Contiguous Turn Consolidation** rule: each uninterrupted speech is a single turn spanning [Start MM:SS - End MM:SS].
- Map every speaker to their identified Role / Name based on video nameplates/titles.
Format:
[MM:SS - MM:SS] **Role / Name**: Spoken utterance (use natural paragraph breaks for long continuous speech)"""

    prompt = f"""# Role & Objective
You are an elite, highly professional executive meeting secretary and transcription specialist.
Analyze this recorded meeting video (utilizing visual slides, on-screen speaker nameplates, lower-third titles, presentation decks, and spoken dialogue audio) and produce a complete, impeccably formatted, executive-ready meeting record.

# Language & Localization Policy
{lang_instruction}

{outline_injection}

# Critical Speaker Consolidation & Transcript Rules
1. **Contiguous Turn Consolidation**:
   - When a participant gives an uninterrupted speech, presentation, report, or remarks, **MUST consolidate their continuous speech into a SINGLE dialogue turn**.
   - The timestamp for that turn MUST span the entire continuous speech duration from start to finish: `[Start MM:SS - End MM:SS]`.
   - **STRICTLY FORBIDDEN** to slice continuous speech by the same speaker into fragmented micro-turns or slide-by-slide snippets.
   - Within the same speaker's turn, organize lengthy content using natural paragraph breaks rather than repeating the speaker's nameplate.
2. **Turn-Taking Transitions**:
   - Only start a new dialogue turn when the floor changes to a different participant (e.g. host introduces the next speaker, attendee asks a question, discussion transitions to another speaker).
3. **Speaker Perspectives in Section 3**:
   - In Section 3, under "Discussion Flow & Speaker Perspectives", explicitly summarize key arguments and inputs organized per speaker:
     - **【Role / Name】**: Key points, metrics, proposals, or directives presented by this speaker.

# Output Structure
Output strictly the following 6 sections in Markdown (DO NOT include any emojis or icons in headings):

{video_schema}
"""

    uploaded_file = None
    try:
        if is_yt:
            print(f"[*] Connecting to YouTube video via Gemini Cloud backbone: {source_str}")
            if use_agentic:
                print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
                part = types.Part(
                    file_data=types.FileData(file_uri=source_str, mime_type="video/mp4"),
                    media_processing=types.MediaProcessing.AGENTIC
                )
            else:
                print("[*] Mode: 📺 High-Speed Static Multimodal Video")
                part = types.Part.from_uri(file_uri=source_str, mime_type="video/mp4")
        else:
            video_path = Path(source_str).resolve()
            if not video_path.is_file():
                raise FileNotFoundError(f"Local video file not found: {video_path}")

            # Check and optimize video size if needed
            upload_target = optimize_video_for_upload(video_path, max_size_mb=250.0)

            print(f"[*] Uploading local video to Google Files API: {upload_target.name}...")
            with safe_ascii_upload_path(upload_target) as safe_path:
                uploaded_file = client.files.upload(file=safe_path)

            print(f"[*] File uploaded (URI: {uploaded_file.uri}). Polling for ACTIVE state...")
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(3)
                uploaded_file = client.files.get(name=uploaded_file.name)

            if uploaded_file.state.name != "ACTIVE":
                raise RuntimeError(f"Video file processing failed with state: {uploaded_file.state.name}")

            mime_type = uploaded_file.mime_type or "video/mp4"
            if use_agentic:
                print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
                part = types.Part(
                    file_data=types.FileData(file_uri=uploaded_file.uri, mime_type=mime_type),
                    media_processing=types.MediaProcessing.AGENTIC
                )
            else:
                print("[*] Mode: 📺 High-Speed Static Multimodal Video")
                part = types.Part(
                    file_data=types.FileData(file_uri=uploaded_file.uri, mime_type=mime_type)
                )

        print(f"[*] Dispatching single-request video analysis to {summary_model}...")
        resp = client.models.generate_content(
            model=summary_model,
            contents=[part, prompt]
        )
        duration = time.time() - t0

        # Extract text content
        output_text = ""
        if hasattr(resp, "text") and resp.text:
            output_text = resp.text
        elif hasattr(resp, "candidates") and resp.candidates:
            for cand in resp.candidates:
                if hasattr(cand, "content") and cand.content:
                    for p in cand.content.parts:
                        if getattr(p, "text", None):
                            output_text += p.text

        # Print token usage accounting
        if hasattr(resp, "usage_metadata") and resp.usage_metadata:
            u = resp.usage_metadata
            print(f"[*] Token Usage: Prompt={getattr(u, 'prompt_token_count', 0)}, "
                  f"Output={getattr(u, 'candidates_token_count', 0)}, "
                  f"Total={getattr(u, 'total_token_count', 0)}")
            if getattr(u, 'thoughts_token_count', None):
                print(f"    (Internal Thought Tokens: {u.thoughts_token_count})")
            if getattr(u, 'tool_use_prompt_token_count', None):
                print(f"    (Tool Navigation Tokens: {u.tool_use_prompt_token_count})")

        print(f"[✓] Video analysis completed in {duration:.1f}s (Output: {len(output_text)} chars)")
        return output_text, duration

    finally:
        if uploaded_file is not None:
            try:
                print(f"[*] Cleaning up ephemeral Files API video ({uploaded_file.name})...")
                client.files.delete(name=uploaded_file.name)
                print(f"[✓] Files API storage cleaned up successfully.")
            except Exception as e:
                print(f"[!] Warning: Failed to delete uploaded video file: {e}")

