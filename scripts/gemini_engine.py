"""
scripts/gemini_engine.py - Gemini Cloud Engine Client & Multimodal Orchestrator.
Handles Stage 1 Cloud ASR, Stage 2 Minutes Generation, Files API management, and Token Accounting.
"""

import os
import re
import time
from pathlib import Path
from google import genai
from google.genai import types

from scripts.audio_utils import format_offset, compress_audio_for_upload, safe_ascii_upload_path


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
        temp_compressed = compress_audio_for_upload(audio_path, bitrate="48k")
        upload_file_path = temp_compressed

    uploaded_file = None
    try:
        with safe_ascii_upload_path(upload_file_path) as safe_upload_path:
            print(f"[*] Uploading audio file to Gemini Files API ({safe_upload_path.stat().st_size / (1024*1024):.1f} MB)...")
            uploaded_file = client.files.upload(file=str(safe_upload_path))
        
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
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
    summary_model: str = "gemini-3.7-flash",
    prompt_template_path: Path = None,
    summary_language: str = None
) -> tuple[str, float]:
    """
    Executes Stage 2: Generates complete 6-section meeting minutes and verbatim transcript.
    """
    t0 = time.time()
    
    glossary_injection = f"""
=== Global Consistency Glossary ===
{global_glossary}
===================================
Strictly adhere to the spelling, names, titles, organizations, and technical terms in the glossary above.
""" if global_glossary else ""

    if summary_language and summary_language.lower() != "auto":
        summary_lang_instruction = (
            f"Output Sections 1 to 5 strictly in the requested target language: '{summary_language}'. "
            f"Ensure section headings and narrative analysis are written naturally and professionally in '{summary_language}'."
        )
    else:
        summary_lang_instruction = (
            "Output Sections 1 to 5 dynamically in the primary language of the user's prompt / conversation "
            "(e.g., Traditional Chinese if the user prompts in Traditional Chinese; English if in English; Japanese if in Japanese). "
            "Render section headings and executive synthesis naturally in that target language."
        )

    if prompt_template_path is None:
        md_candidate = Path(__file__).parent.parent / "assets" / "prompts" / "minutes_prompt.md"
        txt_candidate = Path(__file__).parent.parent / "assets" / "prompts" / "minutes_prompt.txt"
        prompt_template_path = md_candidate if md_candidate.exists() else txt_candidate

    if prompt_template_path.exists():
        template_text = prompt_template_path.read_text(encoding="utf-8")
        prompt = template_text.replace("{audio_filename}", audio_path.name)
        prompt = prompt.replace("{glossary_injection}", glossary_injection)
        prompt = prompt.replace("{raw_transcript_text}", raw_transcript_text)
        prompt = prompt.replace("{summary_language_instruction}", summary_lang_instruction)
    else:
        prompt = f"""You are an elite executive meeting secretary and transcription editor.
Analyze the following draft transcript and glossary:
{glossary_injection}
【Draft Transcript】:
{raw_transcript_text}
【Language Policy】:
{summary_lang_instruction}
Please output a structured Markdown meeting record containing Meeting Metadata, Executive Summary, Key Discussion Topics, Key Decisions, Action Items, and Full Verbatim Transcript.
"""

    print(f"[*] Invoking Gemini model `{summary_model}` for meeting minutes structuring & speaker arbitration...")
    response = client.models.generate_content(
        model=summary_model,
        contents=prompt,
    )
    
    duration = time.time() - t0
    final_text = response.text or ""
    return final_text, duration
