"""
scripts/gemini_engine.py - Gemini Cloud Engine Client & Multimodal Orchestrator.
Handles Stage 1 Cloud ASR, Stage 2 Minutes Generation, Cloud Storage upload staging, and Token Accounting.
Uses Vertex AI with Application Default Credentials exclusively -- no AI Studio API key.
"""

import json
import os
import re
import time
import uuid
from pathlib import Path
import concurrent.futures
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .audio_utils import (
    format_offset,
    compress_audio_for_upload,
    safe_ascii_upload_path,
    should_compress_audio,
    is_youtube_url,
    optimize_video_for_upload,
)
from .gcs_utils import (
    upload_file_to_gcs,
    delete_gcs_blob,
    guess_mime_type,
)


def load_env_file():
    """Load KEY=VALUE pairs (e.g. GOOGLE_CLOUD_PROJECT) from ~/.gemini/.env or current directory .env."""
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



def get_gemini_client(project_id: str = None, location: str = None) -> genai.Client:
    """
    Initialize and return a Google GenAI Client backed by Vertex AI with Application
    Default Credentials (run `gcloud auth application-default login` once beforehand).
    Project/location resolve from arguments, then GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION
    or GCP_PROJECT/GCP_REGION in the environment, then the ADC default project.
    """
    load_env_file()
    project = (
        project_id
        or os.environ.get('GOOGLE_CLOUD_PROJECT')
        or os.environ.get('GCP_PROJECT')
    )
    if not project:
        try:
            import google.auth
            _, project = google.auth.default()
        except Exception:
            project = None
    if not project:
        raise ValueError(
            'Missing Google Cloud project. Set GOOGLE_CLOUD_PROJECT (or GCP_PROJECT) in the '
            'environment, pass it explicitly, or run `gcloud config set project <id>`.'
        )
    region = (
        location
        or os.environ.get('GOOGLE_CLOUD_LOCATION')
        or 'global'
    )
    return genai.Client(vertexai=True, project=project, location=region)


def _unique_raw_blob_name(local_path: Path) -> str:
    """Object key for an ephemeral raw/ upload; unique per call so concurrent uploads never collide."""
    return f"raw/{uuid.uuid4().hex[:12]}_{local_path.name}"


def _parse_offset_to_seconds(val) -> float:
    """Parse Gemini timestamp offset (seconds float/int or string ending in 's') to float."""
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).rstrip("s")
    return float(s) if s else 0.0


def _extract_transcription_parts(parts, lines: list, raw_parts: list) -> bool:
    """
    Extract audio_transcription (diarized turn) or plain text parts from a Gemini
    response's content parts, appending into `lines`/`raw_parts` in place. Shared by
    both the streaming and non-streaming code paths so they format output identically.
    Returns True if any audio_transcription part was found.
    """
    has_at = False
    for part in parts:
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
    return has_at


_DURATION_UNIT_TOKEN_RE = re.compile(r'(\d+)\s*(ms|h|m|s)', re.IGNORECASE)
_DURATION_TOKEN = r'\d+\s*(?:ms|h|m|s)\s*'
_BRACKET_UNIT_PAIR_RE = re.compile(
    rf'\[\s*((?:{_DURATION_TOKEN}){{1,4}})-\s*((?:{_DURATION_TOKEN}){{1,4}})\]',
    re.IGNORECASE,
)


def _unit_duration_to_seconds(token: str) -> float | None:
    """Parse an 'XhYmZsWms'-style duration token (any subset of components) into seconds."""
    matches = _DURATION_UNIT_TOKEN_RE.findall(token)
    if not matches:
        return None
    total = 0.0
    for value, unit in matches:
        value = int(value)
        unit = unit.lower()
        if unit == 'h':
            total += value * 3600
        elif unit == 'm':
            total += value * 60
        elif unit == 's':
            total += value
        elif unit == 'ms':
            total += value / 1000.0
    return total


def normalize_verbatim_timestamps(text: str) -> str:
    """
    Self-healing pass over generated meeting minutes: models occasionally ignore the
    `[MM:SS - MM:SS]` timestamp contract (see prompt_b / video prompt below) and invent
    an alternate duration notation like `[0m0s962ms - 0m3s242ms]` instead. That breaks
    the interactive player's strict bracket parser, collapsing the whole transcript into
    one unparsed block. Rewrite any such pair back into the canonical bracket format the
    player and prompt contract expect; well-formed `[MM:SS - MM:SS]` pairs (no letter
    units) are left untouched.
    """
    def _replace(m: "re.Match[str]") -> str:
        start_sec = _unit_duration_to_seconds(m.group(1))
        end_sec = _unit_duration_to_seconds(m.group(2))
        if start_sec is None or end_sec is None:
            return m.group(0)
        return f"[{format_offset(start_sec)} - {format_offset(end_sec)}]"
    return _BRACKET_UNIT_PAIR_RE.sub(_replace, text)


def transcribe_with_gemini_cloud(
    client: genai.Client,
    audio_path: Path,
    bucket_name: str,
    model_name: str = None,
    compress: bool = True,
    language: str = "auto"
) -> tuple[str, float]:
    """Upload audio to Cloud Storage and run cloud transcription via Vertex AI."""
    load_env_file()
    model_name = model_name or os.environ.get("TRANSCRIBE_MODEL") or "gemini-3.5-transcribe-preview"
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

    gcs_uri = None
    try:
        mime_type = guess_mime_type(upload_file_path)
        with safe_ascii_upload_path(upload_file_path) as safe_upload_path:
            print(f"[*] Uploading audio file to Cloud Storage ({safe_upload_path.stat().st_size / (1024*1024):.1f} MB)...")
            gcs_uri = upload_file_to_gcs(
                local_path=safe_upload_path,
                bucket_name=bucket_name,
                destination_blob_name=_unique_raw_blob_name(safe_upload_path),
                content_type=mime_type,
            )
        file_part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)

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

            request_kwargs = {
                "model": model_name,
                "contents": [file_part],
                "config": types.GenerateContentConfig(
                    audio_transcription_config=types.AudioTranscriptionConfig(**at_kwargs)
                ),
            }
        else:
            prompt = (
                "Transcribe this audio recording completely and accurately. "
                "Include precise turn timestamps in [MM:SS - MM:SS] format for every utterance. "
                "Faithfully preserve the original spoken language and words."
            )
            request_kwargs = {"model": model_name, "contents": [file_part, prompt]}

        try:
            stream = client.models.generate_content_stream(**request_kwargs)
            for chunk in stream:
                if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                    continue
                if _extract_transcription_parts(chunk.candidates[0].content.parts, lines, raw_parts):
                    has_at = True
        except (genai_errors.UnknownApiResponseError, json.JSONDecodeError) as stream_err:
            # The SDK's streaming (SSE) response parser can mis-split multi-byte UTF-8
            # text (e.g. CJK transcripts) across chunk boundaries, corrupting the JSON
            # payload mid-stream. A non-streaming call buffers the complete response
            # before doing a single JSON parse, so it doesn't hit that code path.
            print(f"[!] Streaming transcription failed ({stream_err}); retrying without streaming...")
            lines = []
            raw_parts = []
            has_at = False
            resp = client.models.generate_content(**request_kwargs)
            if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                has_at = _extract_transcription_parts(resp.candidates[0].content.parts, lines, raw_parts)
            elif resp.text:
                raw_parts.append(resp.text)

        if has_at:
            raw_text = "\n\n".join(lines)
        else:
            raw_text = "".join(raw_parts)

        duration = time.time() - t0
        print(f"[*] ✓ Cloud transcription complete in {duration:.1f}s ({len(raw_text)} chars).")
        return raw_text, duration

    finally:
        if gcs_uri:
            try:
                delete_gcs_blob(gcs_uri)
            except Exception:
                pass
        if temp_compressed and temp_compressed.exists():
            temp_compressed.unlink()


def generate_minutes_with_gemini(
    client: genai.Client,
    audio_path: Path,
    raw_transcript_text: str,
    global_glossary: str,
    summary_model: str = None,
    prompt_template_path: Path = None,
    summary_language: str = None
) -> tuple[str, float]:
    """
    Executes Stage 2: Generates complete 6-section meeting minutes and verbatim transcript.
    Uses Dual-Track Concurrency (Track A: Sections 1-5; Track B: Section 6 verbatim localization)
    to minimize wall-clock latency while preserving 100% transcript quality.
    """
    load_env_file()
    summary_model = summary_model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
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
        f"  1. Translate and adapt all section headings (1 to 6), metadata field labels, and table column headers naturally into {target_lang}. This template's English labels (\"Meeting Metadata & Attendees\", \"Executive Summary\", \"Key Discussion Topics\", etc.) are placeholders describing what belongs in each section -- they are NOT literal heading text to copy verbatim. Leaving any heading in English when {target_lang} is not English is a critical error.\n"
        f"  2. **STRICTLY FORBIDDEN to include ANY emojis or icons** (e.g. no 📌, 🎯, 💡, ⚖️, 📋, 🎙️) in any section headings, sub-headings, or table headers. Use clean, plain text markdown headings only (e.g. `## 1. `, `## 2. `)."
    )
    verbatim_lang_instruction = (
        "Faithfully preserve original spoken dialogue and language of each speaker without translation."
    )

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

    # Track A0: Resolve one authoritative Speaker Mapping Table before Sections 1-5 (Track A)
    # and Section 6 (Track B) diverge into independent concurrent calls. Without this, each
    # track may independently guess different names/roles for the same spk_X identifier.
    speaker_table_block = ""
    if prompt_template_path is None:
        speaker_id_prompt = f"""# Role & Objective
You are a speaker identification specialist. Read the draft transcript below and resolve every distinct speaker identifier (`spk_X`, `spk-X`, or `Speaker X`) to its most likely real name or role, using dialogue context (self-introductions, direct address, reporting hierarchies) and the glossary.

{glossary_injection}

---
# Draft Transcript
{raw_transcript_text}

---
# Output Format
Output ONLY a markdown table with this exact structure, no other text or explanation:
| Speaker ID | Role / Title | Name | Organization / Team |
| :--- | :--- | :--- | :--- |
"""
        try:
            speaker_table_block = _call_gemini_track(speaker_id_prompt, "Track A0 (Speaker Identity Resolution)").strip()
        except Exception as e:
            print(f"[!] Warning: Speaker identity pre-resolution failed ({e}); Tracks A and B will resolve speaker identities independently.")
            speaker_table_block = ""

    speaker_table_instruction = (
        f"Use EXACTLY the following pre-determined Speaker Mapping Table (authoritative — do not alter names, roles, or add/remove rows):\n{speaker_table_block}"
        if speaker_table_block else
        "Cross-reference dialogue context and acoustic turns to map every `spk_X` or `Speaker X` identifier to a real person and role:"
    )

    s1_s5_schema = f"""## 1. Meeting Metadata & Attendees
- **Meeting Title**: Inferred or established title
- **Audio Source**: `{audio_path.name}`
- **Estimated Date / Time**: Inferred from context or agenda
- **Chairperson / Host**: Identified meeting leader
- **Speaker Mapping Table**:
  {speaker_table_instruction}
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

    if prompt_template_path is not None:
        template_text = Path(prompt_template_path).read_text(encoding="utf-8")
        prompt = template_text.replace("{audio_filename}", audio_path.name)
        prompt = prompt.replace("{glossary_injection}", glossary_injection)
        prompt = prompt.replace("{raw_transcript_text}", raw_transcript_text)
        prompt = prompt.replace("{summary_language_instruction}", summary_lang_instruction)
        print(f"[*] Invoking Gemini model `{summary_model}` for custom-templated meeting minutes structuring...")
        final_text = normalize_verbatim_timestamps(_call_gemini_track(prompt, "Custom Single-Prompt Template"))
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

    verbatim_speaker_instruction = (
        "Use EXACTLY the following pre-determined Speaker Mapping Table to substitute each speaker ID "
        "with its Role/Name (do not invent alternate identities or spellings):\n" + speaker_table_block
        if speaker_table_block else
        "Map every speaker ID (`spk_X`, `spk-X`, or `Speaker X`) to their identified Role / Name based on "
        "dialogue context (e.g. Chair, Host, Presenter, Department Head, or identified Name)."
    )

    prompt_b = f"""# Role & Objective
You are an elite verbatim meeting transcription editor. Your mission is to transform the draft transcript into Section 6 (Full Verbatim Transcript), localized appropriately into {target_lang}.

{glossary_injection}

---
# Draft Transcript
{raw_transcript_text}

---
# Instructions
1. {verbatim_speaker_instruction}
2. Faithfully preserve all spoken dialogue turns, words, numbers, and chronological order without dropping or truncating sentences.
3. {verbatim_lang_instruction}
4. **Paragraph-Level Turn Consolidation**:
   - When the same speaker continues talking without interruption, do NOT fragment their speech into one turn per raw sentence/utterance from the draft transcript.
   - Instead, regroup it into semantically coherent paragraph-length turns (a natural unit of thought, typically a few sentences), starting a new turn whenever the point/topic shifts or the floor changes to a different speaker.
   - Each paragraph-level turn MUST keep its own accurate `[start - end]` timestamp spanning only that paragraph. NEVER collapse an entire multi-minute speech into a single timestamp range spanning several unrelated paragraphs — every distinct paragraph is its own turn line with its own timestamps.
5. **Mandatory Timestamp Syntax Contract**:
   - Every single dialogue turn MUST strictly preserve and begin with its exact time interval `[MM:SS - MM:SS]` (or `[HH:MM:SS - HH:MM:SS]` for recordings >= 1 hour), using plain colon-separated digits ONLY.
   - STRICTLY FORBIDDEN to omit timestamps, produce plain script format without bracketed times, or invent any alternate duration notation (e.g. `0m0s962ms`, `1h2m3s`) -- an automated parser downstream matches this exact bracket syntax and will corrupt the interactive transcript player if it deviates.
   - Format: `[MM:SS - MM:SS] (or [HH:MM:SS - HH:MM:SS]) **Role / Name**: Spoken utterance`
   - Valid: `[01:15 - 01:45] **Alex Smith (Chair)**: Good morning everyone, let us begin the session.`
   - Invalid: `[0m1s15ms - 0m1s45ms] **Alex Smith**: ...` (wrong notation) or `**Alex Smith**: Good morning...` (missing timestamp).

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
            text_b = normalize_verbatim_timestamps(fut_b.result())

        final_text = f"{text_a.strip()}\n\n---\n\n{text_b.strip()}\n"
        duration = time.time() - t0
        print(f"[*] ✓ [Dual-Track Stage 2] Both tracks successfully completed in {duration:.1f}s.")
        return final_text, duration

    except Exception as e:
        print(f"[!] Dual-track concurrent generation encountered error ({e}). Falling back to single-prompt execution...")
        single_prompt = f"{prompt_a}\n\n---\n\n{prompt_b}"
        final_text = normalize_verbatim_timestamps(_call_gemini_track(single_prompt, "Fallback Single-Prompt"))
        duration = time.time() - t0
        return final_text, duration


def process_video_meeting_end_to_end(
    client: genai.Client,
    video_source: str | Path,
    bucket_name: str = None,
    summary_model: str = None,
    use_agentic: bool = False,
    summary_language: str = None,
    outline_path: Path = None,
) -> tuple[str, float]:
    """
    Process a video meeting end-to-end via Gemini Multimodal Vision (YouTube URL or local video file).
    Produces complete 6 sections (Metadata & Speaker Table, Executive Summary, Topics, Decisions, Action Items, Full Verbatim Transcript)
    in a single request with optional Agentic Video Understanding.
    """
    load_env_file()
    summary_model = summary_model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
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
        f"  1. Translate and adapt all section headings (1 to 6), metadata field labels, and table column headers naturally into {target_lang}. This template's English labels (\"Meeting Metadata & Attendees\", \"Executive Summary\", \"Key Discussion Topics\", etc.) are placeholders describing what belongs in each section -- they are NOT literal heading text to copy verbatim. Leaving any heading in English when {target_lang} is not English is a critical error.\n"
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
- Follow the **Paragraph-Level Turn Consolidation** rule: regroup an uninterrupted speech into semantically coherent paragraph-length turns, each with its own accurate `[Start MM:SS - End MM:SS]` (or `[Start HH:MM:SS - End HH:MM:SS]` for meetings exceeding 1 hour) spanning only that paragraph.
- Map every speaker to their identified Role / Name based on video nameplates/titles.
Format:
[MM:SS - MM:SS] (or [HH:MM:SS - HH:MM:SS]) **Role / Name**: Spoken utterance"""

    prompt = f"""# Role & Objective
You are an elite, highly professional executive meeting secretary and transcription specialist.
Analyze this recorded meeting video (utilizing visual slides, on-screen speaker nameplates, lower-third titles, presentation decks, and spoken dialogue audio) and produce a complete, impeccably formatted, executive-ready meeting record.

# Language & Localization Policy
{lang_instruction}

{outline_injection}

# Critical Speaker Consolidation & Transcript Rules
1. **Paragraph-Level Turn Consolidation**:
   - When a participant gives an uninterrupted speech, presentation, report, or remarks, regroup it into semantically coherent paragraph-length turns (a natural unit of thought, typically a few sentences) — never one turn per raw sentence, and never one giant turn spanning the entire speech.
   - Each paragraph-level turn MUST keep its own accurate timestamp: `[Start MM:SS - End MM:SS]` (or `[Start HH:MM:SS - End HH:MM:SS]` for recordings >= 1 hour) spanning only that paragraph. Do NOT collapse a multi-minute speech into a single timestamp range covering several unrelated paragraphs.
   - **STRICTLY FORBIDDEN** to slice continuous speech by the same speaker into fragmented micro-turns (e.g. one turn per short sentence) or slide-by-slide snippets.
   - Repeat the speaker's Role/Name nameplate on every paragraph-level turn line — every turn line must independently satisfy the timestamp/nameplate format below; the interactive player is responsible for the visual presentation of consecutive same-speaker turns.
2. **Turn-Taking Transitions**:
   - Only start a new dialogue turn when the floor changes to a different participant (e.g. host introduces the next speaker, attendee asks a question, discussion transitions to another speaker).
3. **Speaker Perspectives in Section 3**:
   - In Section 3, under "Discussion Flow & Speaker Perspectives", explicitly summarize key arguments and inputs organized per speaker:
     - **【Role / Name】**: Key points, metrics, proposals, or directives presented by this speaker.
4. **Acoustic Grounding & Strict Timestamp Contract (CRITICAL)**:
   - **Real-Time Acoustic Grounding & Anti-Recitation**: You are transcribing the real-time audio and visual stream of this specific recorded session. Do NOT recite or reproduce text from external knowledge bases, web pages, or pre-training memory. Every dialogue turn MUST strictly represent real-time utterances synchronized with the media player timeline.
   - **Mandatory Timestamp Syntax Contract**: EVERY dialogue turn in Section 6 MUST begin with an exact bracketed time interval `[Start MM:SS - End MM:SS]` (or `[Start HH:MM:SS - End HH:MM:SS]` for recordings >= 1 hour) matching the recording clock, using plain colon-separated digits ONLY.
   - **NEVER OMIT TIMESTAMPS**: Outputting dialogue in plain script format (`**Speaker**: text` without timestamps) is STRICTLY PROHIBITED. Inventing an alternate duration notation (e.g. `0m0s962ms`, `1h2m3s`) instead of the colon-separated format is likewise STRICTLY PROHIBITED -- an automated parser downstream matches this exact bracket syntax and will corrupt the interactive transcript player if it deviates.
   - Valid turn examples:
     `[01:15 - 01:45] **Alex Smith (Chair)**: Good morning everyone, let us begin the session.`
     `[01:15:30 - 01:25:40] **Maria Garcia (Engineering)**: In the second hour of our review, our cloud migration is on schedule...`
   - Invalid turn format:
     `**Alex Smith**: Good morning everyone...` (Missing timestamps will corrupt the player interface).

# Output Structure
Output strictly the following 6 sections in Markdown (DO NOT include any emojis or icons in headings):

{video_schema}
"""

    gcs_uri = None
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
            if not bucket_name:
                raise ValueError("bucket_name is required to analyze a local video file.")

            # Check and optimize video size if needed
            upload_target = optimize_video_for_upload(video_path, max_size_mb=250.0)

            print(f"[*] Uploading local video to Cloud Storage: {upload_target.name}...")
            mime_type = guess_mime_type(upload_target)
            with safe_ascii_upload_path(upload_target) as safe_path:
                gcs_uri = upload_file_to_gcs(
                    local_path=safe_path,
                    bucket_name=bucket_name,
                    destination_blob_name=_unique_raw_blob_name(safe_path),
                    content_type=mime_type,
                )

            if use_agentic:
                print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
                part = types.Part(
                    file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
                    media_processing=types.MediaProcessing.AGENTIC
                )
            else:
                print("[*] Mode: 📺 High-Speed Static Multimodal Video")
                part = types.Part(
                    file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type)
                )

        print(f"[*] Dispatching single-request video analysis to {summary_model}...")
        resp = client.models.generate_content(
            model=summary_model,
            contents=[part, prompt]
        )
        duration = time.time() - t0

        # Extract text content safely
        output_text = ""
        try:
            if resp.text:
                output_text = resp.text
        except Exception:
            pass

        if not output_text and hasattr(resp, "candidates") and resp.candidates:
            for cand in resp.candidates:
                if getattr(cand, "content", None) and getattr(cand.content, "parts", None):
                    for p in cand.content.parts:
                        if getattr(p, "text", None):
                            output_text += p.text

        if not output_text.strip():
            finish_reason = getattr(resp.candidates[0], "finish_reason", "UNKNOWN") if resp.candidates else "NO_CANDIDATE"
            raise RuntimeError(f"Video analysis model returned no text content (finish_reason: {finish_reason})")

        output_text = normalize_verbatim_timestamps(output_text)

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
        if gcs_uri is not None:
            try:
                print(f"[*] Cleaning up ephemeral Cloud Storage video upload...")
                delete_gcs_blob(gcs_uri)
                print(f"[✓] Cloud Storage upload cleaned up successfully.")
            except Exception as e:
                print(f"[!] Warning: Failed to delete uploaded video file: {e}")

