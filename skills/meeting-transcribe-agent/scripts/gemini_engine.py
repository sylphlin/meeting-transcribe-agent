"""
scripts/gemini_engine.py - Gemini Cloud Engine Client & Multimodal Orchestrator.
Handles Stage 1 Cloud ASR, Stage 2 Minutes Generation, Cloud Storage upload staging, and Token Accounting.
Uses Vertex AI with Application Default Credentials exclusively -- no AI Studio API key.
"""

import concurrent.futures
import os
from pathlib import Path
import random
import re
import time
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from scripts.audio_utils import (
    format_offset,
    safe_ascii_upload_path,
    is_youtube_url,
    optimize_video_for_upload,
    fix_mojibake_filename,
)
from scripts.canonicalizer import consolidate_meeting_minutes
from scripts.gcs_utils import (
    upload_file_to_gcs,
    delete_gcs_blob,
    guess_mime_type,
)


def load_env_file():
    """Load KEY=VALUE pairs (e.g. GOOGLE_CLOUD_PROJECT) from ~/.gemini/.env or current directory .env."""
    _resolved_file = Path(__file__).resolve()
    candidates = [
        Path.home() / ".gemini" / ".env",
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
        *[p / ".env" for p in _resolved_file.parents[:5]],
    ]
    for env_path in candidates:
        try:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.split("#")[0].strip().strip("\"'")
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
    """Deterministic object key under raw/ for SHA-256 / gdrive_md5 cache hits and 2-day Lifecycle cleanup."""
    return f"raw/{local_path.name}"


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


def call_gemini_with_retry(
    fn,
    *args,
    max_retries: int = 4,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0,
    op_name: str = "Gemini API call",
    **kwargs,
):
    """
    Execute a Gemini API call with exponential backoff and randomized jitter.
    Automatically retries on HTTP 429 (Resource Exhausted / Rate Limit), 500, 502, 503, 504,
    and transient network errors.
    Strictly fails fast on HTTP 401 and 403 (Authentication / Permission Denied) per Rule 8.
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (401, 403):
                # Fail-fast immediately on auth / permission errors (Rule 8)
                raise
            err_str = str(e)
            is_retryable = (
                code in (429, 500, 502, 503, 504)
                or "RESOURCE_EXHAUSTED" in err_str
                or "quota" in err_str.lower()
                or "rate limit" in err_str.lower()
                or "overloaded" in err_str.lower()
            )
            if is_retryable:
                last_err = e
                if attempt == max_retries - 1:
                    raise
                jitter_val = random.uniform(-0.2, 0.2)
                sleep_sec = max(0.5, (initial_delay * (backoff_factor ** attempt)) * (1.0 + jitter_val))
                status_desc = f"HTTP {code}" if code else "Resource Exhausted / Rate Limit"
                print(f"[!] {op_name}: encountered {status_desc}. Retrying in {sleep_sec:.1f}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(sleep_sec)
            else:
                raise
        except (ConnectionError, TimeoutError, Exception) as e:
            err_name = type(e).__name__
            is_net_err = (
                "Timeout" in err_name
                or "Connect" in err_name
                or "Network" in err_name
                or "Reset" in err_name
            )
            if is_net_err:
                last_err = e
                if attempt == max_retries - 1:
                    raise
                jitter_val = random.uniform(-0.2, 0.2)
                sleep_sec = max(0.5, (initial_delay * (backoff_factor ** attempt)) * (1.0 + jitter_val))
                print(f"[!] {op_name}: network error ({e}). Retrying in {sleep_sec:.1f}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(sleep_sec)
            else:
                raise
    if last_err:
        raise last_err


def transcribe_with_gemini_cloud(
    client: genai.Client,
    audio_path: Path,
    bucket_name: str,
    model_name: str = None,
    compress: bool = True,
    language: str = "auto",
    project_id: str = None,
    stt_location: str = None,
    min_speakers: int = 2,
    max_speakers: int = 10,
) -> tuple[str, float]:
    """
    Execute Stage 1 cloud speech transcription and diarization via Dual-Pass Chirp
    (Track A global diarization + Track B parallel word timestamps).
    """
    load_env_file()
    from scripts.chirp3_engine import transcribe_with_chirp3_dual_pass, validate_chirp_model

    resolved_model = validate_chirp_model(model_name)
    return transcribe_with_chirp3_dual_pass(
        audio_path=audio_path,
        bucket_name=bucket_name,
        project_id=project_id,
        stt_location=stt_location,
        model_name=resolved_model,
        language=language,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )


_TURN_LINE_PREFIX_RE = re.compile(
    r"^(\[\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*\]\s*\*\*([^*]+)\*\*\s*:\s*)(.+)$"
)
_TURN_BRACKET_EXTRACT_RE = re.compile(
    r"^\s*\[\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*\]\s*(?:\*\*[^*]+\*\*\s*:\s*)?(.+)$"
)


def refine_verbatim_transcript_chunks(
    raw_transcript_text: str,
    sections_1_5: str,
    global_glossary: str,
    target_lang: str,
    call_track_fn,
    chunk_size: int = 60,
) -> str:
    """
    Proofread and align the written orthographic script and domain terminology of Section 6
    dialogue turns in parallel chunks. Also performs LLM semantic paragraph segmentation on
    long monologues (using inline `<PARA>` markers) and re-projects physical word-level
    timestamps onto each resulting paragraph via AlignmentEngine.
    """
    from scripts.alignment_engine import AlignmentEngine
    from scripts.canonicalizer import parse_timestamp_to_seconds
    from scripts.chirp3_engine import get_last_aligned_turns

    turn_lines = [line.strip() for line in raw_transcript_text.splitlines() if line.strip()]
    if not turn_lines:
        return raw_transcript_text

    aligned_turns_cache = get_last_aligned_turns()
    turn_words_by_ts: dict[tuple[str, str], list[list[dict]]] = {}
    for aturn in aligned_turns_cache:
        t_st = format_offset(aturn.get("start") or 0.0)
        t_et = format_offset(aturn.get("end") or 0.0)
        w_items = aturn.get("word_items") or []
        if w_items:
            turn_words_by_ts.setdefault((t_st, t_et), []).append(w_items)

    section_1_excerpt = (
        sections_1_5.split("## 2.")[0].strip()
        if "## 2." in sections_1_5
        else sections_1_5[:2000].strip()
    )
    glossary_block = (
        f"=== Global Consistency Glossary ===\n{global_glossary.strip()}\n===================================\n"
        if global_glossary and global_glossary.strip()
        else ""
    )

    chunks = [turn_lines[i : i + chunk_size] for i in range(0, len(turn_lines), chunk_size)]
    num_chunks = len(chunks)

    def _expand_turn_with_semantic_paragraphs(
        prefix: str,
        ts_pair: tuple[str, str],
        speaker_str: str,
        new_utt: str,
    ) -> list[str]:
        if "<PARA>" not in new_utt:
            return [f"{prefix}{new_utt}"]
        paras = [p.strip() for p in re.split(r"\s*<PARA>\s*", new_utt) if p.strip()]
        if len(paras) <= 1:
            clean_single = paras[0] if paras else new_utt.replace("<PARA>", "").strip()
            return [f"{prefix}{clean_single}"]

        turn_words = []
        if ts_pair in turn_words_by_ts and turn_words_by_ts[ts_pair]:
            turn_words = turn_words_by_ts[ts_pair][0]

        fallback_st = parse_timestamp_to_seconds(ts_pair[0])
        fallback_et = parse_timestamp_to_seconds(ts_pair[1])
        projected = AlignmentEngine().reproject_semantic_paragraphs(
            turn_words=turn_words,
            paragraphs=paras,
            fallback_start=fallback_st,
            fallback_end=fallback_et,
        )
        expanded_lines: list[str] = []
        for p_item in projected:
            p_st_str = format_offset(p_item["start"])
            p_et_str = format_offset(p_item["end"])
            expanded_lines.append(f"[{p_st_str} - {p_et_str}] **{speaker_str}**: {p_item['text']}")
        return expanded_lines

    def _process_chunk(chunk_idx: int) -> tuple[int, list[str]]:
        chunk_lines = chunks[chunk_idx]
        chunk_body = "\n".join(chunk_lines)
        prompt_chunk = f"""# Role & Objective
Proofread and align the orthographic script, domain terminology, and semantic paragraph breaks of the following verbatim transcript segment.

{glossary_block}
# Reference Context (Section 1 Metadata & Entity Tables)
{section_1_excerpt}

# Strict Rules
1. Preserve every line's exact bracketed timestamp `[MM:SS - MM:SS]` (or `[HH:MM:SS - HH:MM:SS]`) and speaker tag `**Speaker X**:` (or `**spk_X**:`) at the start of the line.
2. Do NOT merge, drop, reorder, or add new dialogue lines. Output the exact same number of lines in the exact same order.
3. Keep the original spoken language and words of each participant without translating between different spoken languages.
4. Align the written orthographic script (such as Traditional vs. Simplified characters) to match the script used in the Reference Context ({target_lang}).
5. Correct acoustic phonetic mishearings of participant names, titles, organizations, and technical terms using the Reference Context and Global Consistency Glossary.
6. Semantic Paragraph Segmentation for Long Turns: When a single turn line contains a long uninterrupted monologue or presentation (more than 4 sentences or 300 characters) that covers multiple sub-topics, insert the inline marker ` <PARA> ` at natural semantic topic transitions within that same line (keep it on one line). Leave short conversational turns without ` <PARA> `.
7. Output ONLY the refined transcript lines. Do not include Markdown code fences or commentary.

# Transcript Segment to Proofread
{chunk_body}
"""
        try:
            resp_text = normalize_verbatim_timestamps(
                call_track_fn(
                    prompt_chunk,
                    f"Stage 2 Verbatim Script & Terminology Proofreading ({chunk_idx + 1}/{num_chunks})",
                )
            )
        except Exception as e:
            print(f"[!] Warning: Verbatim proofreading chunk {chunk_idx + 1}/{num_chunks} failed ({e}). Keeping raw turns.")
            return chunk_idx, chunk_lines

        by_ts: dict[tuple[str, str], list[str]] = {}
        ordered_utterances: list[str] = []
        for out_line in resp_text.splitlines():
            m_out = _TURN_BRACKET_EXTRACT_RE.match(out_line.strip())
            if m_out:
                ts_pair = (m_out.group(1), m_out.group(2))
                utt_clean = m_out.group(3).strip()
                if utt_clean:
                    by_ts.setdefault(ts_pair, []).append(utt_clean)
                    ordered_utterances.append(utt_clean)

        refined_chunk: list[str] = []
        for line_i, orig_line in enumerate(chunk_lines):
            m_orig = _TURN_LINE_PREFIX_RE.match(orig_line)
            if not m_orig:
                refined_chunk.append(orig_line)
                continue
            prefix = m_orig.group(1)
            ts_pair = (m_orig.group(2), m_orig.group(3))
            speaker_str = m_orig.group(4).strip()
            if ts_pair in by_ts and by_ts[ts_pair]:
                new_utt = by_ts[ts_pair].pop(0)
                refined_chunk.extend(
                    _expand_turn_with_semantic_paragraphs(prefix, ts_pair, speaker_str, new_utt)
                )
            elif len(ordered_utterances) == len(chunk_lines):
                new_utt = ordered_utterances[line_i]
                refined_chunk.extend(
                    _expand_turn_with_semantic_paragraphs(prefix, ts_pair, speaker_str, new_utt)
                )
            else:
                refined_chunk.append(orig_line)

        return chunk_idx, refined_chunk

    max_workers = min(num_chunks, 4)
    results: list[tuple[int, list[str]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_chunk, idx) for idx in range(num_chunks)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda x: x[0])
    all_refined: list[str] = []
    for _, c_lines in results:
        all_refined.extend(c_lines)

    return "\n\n".join(all_refined)


def generate_minutes_with_gemini(
    client: genai.Client,
    audio_path: Path,
    raw_transcript_text: str,
    global_glossary: str,
    summary_model: str = None,
    prompt_template_path: Path = None,
    summary_language: str = None,
    srt_path: Path | str = None,
) -> tuple[str, float]:
    """
    Executes Stage 2: Generates complete 6-section meeting minutes and verbatim transcript.
    Synthesizes structured executive sections 1 through 5 (Metadata & Speaker Mapping Table,
    Executive Summary, Key Topics with Speaker Perspectives, Decisions, and Action Items)
    plus the localized Section 6 heading via Gemini 3.8 Flash.
    Section 6 verbatim dialogue turns are proofread in parallel chunks and deterministically
    assembled downstream by Python with canonical speaker consolidation.
    """
    load_env_file()
    summary_model = summary_model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
    t0 = time.time()
    clean_audio_name = fix_mojibake_filename(audio_path.name)
    
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
        f"  2. **STRICTLY FORBIDDEN to include ANY emojis or icons** (e.g. no 📌, 🎯, 💡, ⚖️, 📋, 🎙️) in any section headings, sub-headings, or table headers. Use clean, plain text markdown headings only (e.g. `## 1. `, `## 2. `).\n"
        f"- **Section 6 Heading (CRITICAL)**:\n"
        f"  At the very end of your response, output ONLY the localized Markdown heading for Section 6 (e.g., `## 6. Full Verbatim Transcript` in English, `## 6. 完整逐字記錄` in Traditional Chinese, `## 6. 全文逐字録` in Japanese), followed by nothing else.\n"
        f"  Section 6 verbatim dialogue turns are deterministically assembled downstream by Python to preserve 100% physical acoustic clock fidelity and avoid output token limits."
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
        print(f"[*] [Stage 2] Launching {label} with `{summary_model}` (thinking_budget=0)...")
        resp = call_gemini_with_retry(
            lambda: client.models.generate_content(**gen_kwargs),
            op_name=label
        )
        return resp.text or ""

    if prompt_template_path is not None:
        template_text = Path(prompt_template_path).read_text(encoding="utf-8")
        prompt = template_text.replace("{audio_filename}", clean_audio_name)
        prompt = prompt.replace("{glossary_injection}", glossary_injection)
        prompt = prompt.replace("{raw_transcript_text}", raw_transcript_text)
        prompt = prompt.replace("{summary_language_instruction}", summary_lang_instruction)
        print(f"[*] Invoking Gemini model `{summary_model}` for custom-templated meeting minutes structuring...")
        final_text = normalize_verbatim_timestamps(_call_gemini_track(prompt, "Custom Single-Prompt Template"))
        duration = time.time() - t0
        return final_text, duration

    prompt = f"""# Role & Objective
You are an elite, highly professional executive meeting secretary and intelligence analyst. Your mission is to analyze the draft transcript of a recorded meeting and produce Sections 1 to 5 of an impeccably formatted, executive-ready meeting record in Markdown, followed by the localized Section 6 heading.

{glossary_injection}

---
# Draft Transcript
{raw_transcript_text}

---
# Language & Label Policy
{summary_lang_instruction}

---
# Output Structure (Sections 1 to 5 + Section 6 Heading ONLY)
Output strictly and exclusively Sections 1 to 5, ending immediately with the localized Section 6 heading (DO NOT include any emojis or icons in headings):

## 1. Meeting Metadata & Attendees
- **Meeting Title**: (Inferred or established title)
- **Audio Source**: `{clean_audio_name}`
- **Estimated Date / Time**: (Inferred from context or agenda)
- **Chairperson / Host**: (Identified meeting leader)
- **Speaker Mapping Table**:
  Cross-reference dialogue context, self-introductions, titles, organizations, and acoustic turns to map every `spk_X` or `Speaker X` identifier to a real person and role (if an acoustic ID is shared across different segments, provide the approximate Time Range):
  | Speaker ID | Time Range (optional if unique) | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- | :--- |
  | `spk_0` | `00:00 - 00:04` | Meeting Host | Alice Smith | Executive Board |
  | `spk_0` | `07:35 - 10:20` | Keynote Speaker | Bob Jones | Architecture Dept |
- **Phonetic & Entity Corrections Table**:
  Identify any proper names, participant names, or technical terms in the draft transcript that were mistranscribed due to acoustic phonetic slips or rare name mishearings:
  | Mistranscribed Term | Corrected Name / Term | Target Speaker / Context |
  | :--- | :--- | :--- |

## 2. Executive Summary
- A high-level, 300–400 word executive overview synthesizing core purpose, major themes, decisions, and outcomes.

## 3. Key Discussion Topics & Agenda Items
- Structured breakdown for each topic discussed:
  - **Context & Motivation**: Background and why this issue was raised.
  - **Key Arguments & Data**: Evidence, metrics, or points presented by participants.
  - **Discussion Flow & Speaker Perspectives**: Contributions organized per speaker (【Role / Name】: Key arguments, metrics, or positions).
  - **Outcome / Consensus**: Conclusion reached on this specific topic.

## 4. Key Decisions & Resolutions
- Bulleted list of formal decisions, policy directives, approved motions, or consensus reached.

## 5. Action Items & Next Steps
- Structured Markdown table assigning clear ownership and timelines:
  | # | Action Item / Task | Owner / Assignee | Due Date / Timeline | Status / Notes |
  | :--- | :--- | :--- | :--- | :--- |
  | 1 | [Clear, actionable task description] | [Name / Role] | [Timeline] | [Notes] |

## 6. Full Verbatim Transcript
(Output ONLY the localized Section 6 heading translated into the target language. Stop immediately after this heading line. Do NOT output any transcript lines!)
"""

    sections_1_5 = _call_gemini_track(prompt, "Stage 2 Executive Synthesis & Speaker Identification")
    if not re.search(r'(?m)^##\s*6\.\s*', sections_1_5):
        sections_1_5 = f"{sections_1_5.strip()}\n\n## 6. Full Verbatim Transcript"

    refined_transcript = refine_verbatim_transcript_chunks(
        raw_transcript_text=raw_transcript_text,
        sections_1_5=sections_1_5,
        global_glossary=global_glossary,
        target_lang=target_lang,
        call_track_fn=_call_gemini_track,
    )

    combined_raw = f"{sections_1_5.strip()}\n\n{refined_transcript.strip()}\n"
    print(f"[*] Consolidating canonical speaker identities, entity corrections, and sequential turns...")
    final_text = consolidate_meeting_minutes(combined_raw, srt_path=srt_path)
    duration = time.time() - t0
    print(f"[*] ✓ [Stage 2] Meeting minutes structuring successfully completed in {duration:.1f}s.")
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
            print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
            part = types.Part(
                file_data=types.FileData(file_uri=source_str, mime_type="video/mp4"),
                media_processing=types.MediaProcessing.AGENTIC
            )
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

            print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
            part = types.Part(
                file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
                media_processing=types.MediaProcessing.AGENTIC
            )

        print(f"[*] Dispatching single-request video analysis to {summary_model}...")
        resp = call_gemini_with_retry(
            lambda: client.models.generate_content(model=summary_model, contents=[part, prompt]),
            op_name=f"End-to-end video analysis ({summary_model})"
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


def analyze_video_with_transcript(
    client: genai.Client,
    video_path: str | Path,
    raw_transcript_text: str,
    bucket_name: str = None,
    summary_model: str = None,
    use_agentic: bool = False,
    summary_language: str = None,
    outline_path: Path = None,
    srt_path: Path | str = None,
) -> tuple[str, float]:
    """
    Stage 2 of the Local Video Pipeline: Multimodal Vision + Transcript Fusion.
    Takes the local video (optimized to 720p and uploaded to Cloud Storage) together with the
    Stage 1 acoustic verbatim transcript. Gemini 3.8 Flash inspects visual slides, speaker nameplates,
    presentation decks, attendee video feeds, and the verbatim transcript to:
      1. Accurately map participant identities (Speaker 1, Speaker 2 -> Real Name, Title, Organization)
         in the Section 1 Speaker Mapping Table.
      2. Synthesize structured executive sections 1 through 5 (Metadata & Attendees, Executive Summary,
         Key Topics with Speaker Perspectives, Decisions, and Action Items).
      3. Output the localized Section 6 heading.
    Section 6 verbatim transcript turns are deterministically assembled downstream by Python
    to maintain 100% physical acoustic clock fidelity and avoid output token limits.
    """
    load_env_file()
    summary_model = summary_model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
    t0 = time.time()
    video_p = Path(video_path).resolve()
    if not video_p.is_file():
        raise FileNotFoundError(f"Local video file not found: {video_p}")
    if not bucket_name:
        raise ValueError("bucket_name is required to analyze a local video file.")

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
        f"- **Section 6 Heading**:\n"
        f"  At the very end of your response, output ONLY the localized Markdown heading for Section 6 (e.g., `## 6. Full Verbatim Transcript` in English, `## 6. 完整逐字記錄` in Traditional Chinese, `## 6. 全文逐字録` in Japanese), followed by nothing else."
    )

    clean_video_name = fix_mojibake_filename(video_p.name)
    s1_s5_schema = f"""## 1. Meeting Metadata & Attendees
- **Meeting Title**: (Inferred from video slides, agenda, or dialogue)
- **Source**: {clean_video_name}
- **Estimated Date / Time**: (Inferred from slides or dialogue)
- **Chairperson / Host**: (Identified meeting leader)
- **Speaker Mapping Table**:
  Cross-reference the draft transcript's dialogue turns with video frames, presentation slides, attendee video boxes, nameplates, and lower-third titles.
  If multiple people share the same acoustic ID across different time segments (under-clustering), specify the approximate Time Range for each segment:
  | Speaker ID | Time Range (optional if unique) | Role / Title | Name | Organization / Department | Remarks / Context |
  | :--- | :--- | :--- | :--- | :--- | :--- |
  | `spk_0` | `00:00 - 00:04` | Meeting Host | Alice Smith | Executive Board | Opening remarks |
  | `spk_0` | `07:35 - 10:20` | Keynote Speaker | Bob Jones | Architecture Dept | System overview presentation |
- **Phonetic & Entity Corrections Table**:
  Cross-reference visual slide text, nameplates, and titles with the acoustic draft transcript. Identify any proper names, participant names, or technical terms that were mistranscribed due to acoustic phonetic slips or rare name mishearings:
  | Mistranscribed Term | Corrected Name / Term | Target Speaker / Context |
  | :--- | :--- | :--- |

## 2. Executive Summary
- A high-level, 300–400 word executive overview synthesizing core purpose, major themes, decisions, and outcomes.

## 3. Key Discussion Topics & Agenda Items
- Structured breakdown for each topic discussed:
  - **Context & Motivation**: Background and why this issue was raised.
  - **Key Arguments & Data**: Evidence, metrics, or points presented by participants (incorporating on-screen presentation slides, architecture diagrams, or demo screens).
  - **Discussion Flow & Speaker Perspectives**: Contributions organized per speaker (【Role / Name】: Key arguments, metrics, or positions).
  - **Outcome / Consensus**: Conclusion reached on this specific topic.

## 4. Key Decisions & Resolutions
- Bulleted list of formal decisions, policy directives, approved motions, or consensus reached.

## 5. Action Items & Next Steps
- Structured Markdown table assigning clear ownership and timelines:
  | # | Action Item / Task | Owner / Assignee | Due Date / Timeline | Status / Notes |
  | :--- | :--- | :--- | :--- | :--- |

## 6. Full Verbatim Transcript
(Output ONLY the localized Section 6 heading translated into {target_lang}. Stop immediately after this heading line. Do NOT output any transcript lines!)"""

    prompt = f"""# Role & Objective
You are an elite, highly professional executive meeting secretary and multimodal video intelligence specialist.
Analyze this recorded meeting video (utilizing visual slides, on-screen speaker nameplates, lower-third titles, presentation decks, attendee video feeds, and the provided acoustic draft transcript) and produce Sections 1 to 5 of an impeccably formatted, executive-ready meeting record.

# Language & Localization Policy
{lang_instruction}

{outline_injection}

# Ground-Truth Acoustic Draft Transcript
The following is the verbatim acoustic transcript with physical timestamps and initial speaker cluster IDs from Stage 1 speech recognition:
---
{raw_transcript_text}
---

# Critical Visual & Speaker Mapping Instructions
1. **Visual Speaker Grounding & Mapping**:
   - Cross-reference the draft transcript's dialogue turns with video frames, presentation slides, attendee video boxes, nameplates, and lower-third titles.
   - Accurately map every speaker ID from the draft transcript (e.g. `Speaker 1`, `Speaker 2`, `spk_0`, `spk_1`) to their real Name, Official Title / Role, and Organization / Department.
   - When a single acoustic cluster ID (e.g. `spk_0`) is erroneously reused for multiple distinct speakers across different parts of the meeting (acoustic under-clustering), list separate rows with distinct Time Ranges (e.g. `00:00 - 00:04` for Alice Smith, `07:35 - 10:20` for Bob Jones) in the Speaker Mapping Table.
   - Populate the **Speaker Mapping Table** in Section 1 with these mapped identities.
2. **Slide & Visual Deck Synthesis**:
   - In Section 3 (Key Discussion Topics & Agenda Items), incorporate key data, metrics, architecture diagrams, and slide points visible on screen.
   - Under "Discussion Flow & Speaker Perspectives", explicitly organize contributions per mapped speaker (`【Role / Name】: ...`).
3. **Sections 1 to 5 ONLY**:
   - Output strictly and exclusively Sections 1 to 5, followed by the localized Section 6 heading.
   - **DO NOT** output any dialogue lines in Section 6 (the verbatim transcript will be deterministically assembled downstream to preserve physical timestamps).
   - Stop immediately after the Section 6 heading line.

# Output Structure
Output strictly the following structure in Markdown (DO NOT include any emojis or icons in headings):

{s1_s5_schema}
"""

    gcs_uri = None
    try:
        # Check and optimize video size if needed (e.g. 720p H.264)
        upload_target = optimize_video_for_upload(video_p, max_size_mb=250.0)

        print(f"[*] Uploading local video to Cloud Storage: {upload_target.name}...")
        mime_type = guess_mime_type(upload_target)
        with safe_ascii_upload_path(upload_target) as safe_path:
            gcs_uri = upload_file_to_gcs(
                local_path=safe_path,
                bucket_name=bucket_name,
                destination_blob_name=_unique_raw_blob_name(safe_path),
                content_type=mime_type,
            )

        print("[*] Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
        part = types.Part(
            file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
            media_processing=types.MediaProcessing.AGENTIC,
        )

        print(f"[*] Dispatching multimodal video + transcript fusion analysis to {summary_model}...")
        resp = call_gemini_with_retry(
            lambda: client.models.generate_content(model=summary_model, contents=[part, prompt]),
            op_name=f"Multimodal video + transcript fusion ({summary_model})"
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

        # Defensively ensure Section 6 heading exists before stitching
        if not re.search(r'(?m)^##\s*6\.\s*', output_text):
            output_text = f"{output_text.strip()}\n\n## 6. Full Verbatim Transcript"

        def _call_video_proofread_track(p_content: str, label: str) -> str:
            gen_kwargs = {"model": summary_model, "contents": p_content}
            if hasattr(types, "ThinkingConfig"):
                try:
                    gen_kwargs["config"] = types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_budget=0)
                    )
                except Exception:
                    pass
            print(f"[*] [Stage 2] Launching {label} with `{summary_model}` (thinking_budget=0)...")
            r_obj = call_gemini_with_retry(
                lambda: client.models.generate_content(**gen_kwargs),
                op_name=label,
            )
            return r_obj.text or ""

        refined_transcript = refine_verbatim_transcript_chunks(
            raw_transcript_text=raw_transcript_text,
            sections_1_5=output_text,
            global_glossary="",
            target_lang=target_lang,
            call_track_fn=_call_video_proofread_track,
        )

        combined_raw = f"{output_text.strip()}\n\n{refined_transcript.strip()}\n"
        print(f"[*] Consolidating canonical speaker identities, entity corrections, and sequential turns...")
        final_text = consolidate_meeting_minutes(combined_raw, srt_path=srt_path)

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

        print(f"[✓] Multimodal video + transcript analysis completed in {duration:.1f}s (Output: {len(final_text)} chars)")
        return final_text, duration

    finally:
        if gcs_uri is not None:
            try:
                print(f"[*] Cleaning up ephemeral Cloud Storage video upload...")
                delete_gcs_blob(gcs_uri)
                print(f"[✓] Cloud Storage upload cleaned up successfully.")
            except Exception as e:
                print(f"[!] Warning: Failed to delete uploaded video file: {e}")


