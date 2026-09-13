"""
scripts/glossary.py - Dual-Track Meeting Consistency Glossary Extraction.
Handles 1M Audio Pre-scan and Meeting Outline/Agenda Terminology Mining.
"""

import re
import time
import uuid
from pathlib import Path
from google import genai
from google.genai import types
from .audio_utils import safe_ascii_upload_path
from .gcs_utils import upload_file_to_gcs, delete_gcs_blob, guess_mime_type


def extract_keywords_from_glossary(glossary_text: str, max_keywords: int = 40) -> str:
    """
    Extract top entity keywords from glossary text to formulate an ASR prompt.
    Parses bullet terms and table data rows structurally without hardcoded language dictionaries.
    """
    if not glossary_text:
        return ""

    extracted = []
    in_table = False
    table_separator_seen = False

    for line in glossary_text.splitlines():
        line_strip = line.strip()
        if not line_strip or line_strip.startswith("#"):
            in_table = False
            table_separator_seen = False
            continue

        # Structural bullet item: - **Entity**: Description or * **Entity**
        bullet_match = re.match(r'^\s*[-*]\s+\*\*([^*]+)\*\*', line_strip)
        if bullet_match:
            term = re.sub(r'[\(（].*?[\)）]', '', bullet_match.group(1)).strip()
            if 2 <= len(term) <= 30 and term not in extracted:
                extracted.append(term)
            continue

        # Structural markdown table
        if line_strip.startswith("|") and line_strip.endswith("|"):
            cells = [c.strip() for c in line_strip[1:-1].split("|")]
            # Check if this line is the markdown separator row (|:---|:---|)
            if all(re.match(r'^:?-+:?$', c) for c in cells if c):
                table_separator_seen = True
                in_table = True
                continue

            # If inside table data (after separator row), extract first valid entity cell
            if table_separator_seen and in_table:
                for c in cells:
                    clean = re.sub(r'[\(（].*?[\)）]', '', c).replace("*", "").replace("`", "").strip()
                    if 2 <= len(clean) <= 30 and clean not in extracted and not re.match(r'^:?-+:?$', clean):
                        extracted.append(clean)
                        break
        else:
            in_table = False
            table_separator_seen = False

    top_terms = extracted[:max_keywords]
    if not top_terms:
        return ""
    return ", ".join(top_terms) + "."



def extract_global_consistency_glossary(
    client: genai.Client,
    audio_path: Path,
    bucket_name: str,
    outline_path: str | None = None,
    model: str = None,
    force: bool = False,
    prompt_template_path: Path = None,
    compress_fn = None
) -> tuple[str, str]:
    """
    Dual-track extraction:
    Track 1: 1M Audio Pre-scan with Gemini Flash.
    Track 2: Parsing optional user-provided agenda/outline document.
    Returns: (full_glossary_markdown, prompt_keywords)
    """
    model = model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
    glossary_file = audio_path.parent / f"glossary_{audio_path.stem}.md"
    alt_glossary = audio_path.parent / f"{audio_path.stem}_glossary.md"
    target_cache = glossary_file if glossary_file.exists() else alt_glossary
    
    if not force and target_cache.exists():
        print(f"[*] ⚡ Found cached consistency glossary: {target_cache.name}, loading directly.")
        cached_content = target_cache.read_text(encoding="utf-8")
        keywords = extract_keywords_from_glossary(cached_content)
        return cached_content, keywords

    print(f"\n========================================================")
    print(f"📚 [Glossary] Launching dual-track consistency glossary mining...")
    print(f"========================================================")
    t0 = time.time()

    user_outline_text = ""
    if outline_path:
        outline_file = Path(outline_path)
        if outline_file.exists():
            print(f"[*] Importing external meeting outline/agenda: {outline_file.name}")
            try:
                user_outline_text = outline_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[!] Warning: Failed to read outline file ({e}), proceeding with audio-only mining.")

    outline_section = f"\n=== External Meeting Notice & Agenda Outline ===\n{user_outline_text}\n" if user_outline_text else ""

    if prompt_template_path is None:
        md_candidate = Path(__file__).parent.parent / "assets" / "prompts" / "audio_glossary_prompt.md"
        txt_candidate = Path(__file__).parent.parent / "assets" / "prompts" / "audio_glossary_prompt.txt"
        prompt_template_path = md_candidate if md_candidate.exists() else txt_candidate

    if prompt_template_path.exists():
        template_text = prompt_template_path.read_text(encoding="utf-8")
        prompt = template_text.replace("{outline_section}", outline_section)
    else:
        prompt = f"""You are an elite meeting intelligence and terminology extraction specialist.
Analyze this audio recording and extract an authoritative Global Consistency Glossary in Markdown covering:
1. Participants & Speaker Names
2. Organizations, Divisions & Teams
3. Domain Terminology, Projects, Products & Acronyms
4. Core Discussion Topics & Agenda Themes
{outline_section}
"""

    compressed_audio = None
    gcs_uri = None
    glossary_content = ""

    try:
        if compress_fn:
            compressed_audio = compress_fn(audio_path, bitrate="48k")
        else:
            compressed_audio = audio_path

        mime_type = guess_mime_type(compressed_audio)
        with safe_ascii_upload_path(compressed_audio) as safe_upload_path:
            print(f"[*] Uploading lightweight audio to Cloud Storage for entity discovery ({safe_upload_path.stat().st_size / (1024*1024):.1f} MB)...")
            blob_name = f"raw/{uuid.uuid4().hex[:12]}_{safe_upload_path.name}"
            gcs_uri = upload_file_to_gcs(
                local_path=safe_upload_path,
                bucket_name=bucket_name,
                destination_blob_name=blob_name,
                content_type=mime_type,
            )

        file_part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)
        response = client.models.generate_content(
            model=model,
            contents=[file_part, prompt],
        )
        glossary_content = response.text or ""
        duration = time.time() - t0
        print(f"[*] ✓ Global consistency glossary extracted in {duration:.1f}s ({len(glossary_content)} chars).")

        glossary_file.write_text(glossary_content, encoding="utf-8")
        print(f"[*] 💾 Glossary cached to: {glossary_file}")

    except Exception as e:
        print(f"[!] Warning: Glossary mining encountered an issue ({e}), using default entity guide.")
        glossary_content = user_outline_text or "General meeting conversation with technical terms."
    finally:
        if gcs_uri:
            try:
                delete_gcs_blob(gcs_uri)
            except Exception:
                pass
        if compressed_audio and compressed_audio != audio_path and compressed_audio.exists():
            compressed_audio.unlink()

    keywords = extract_keywords_from_glossary(glossary_content)
    return glossary_content, keywords
