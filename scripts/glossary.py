"""
scripts/glossary.py - Dual-Track Meeting Consistency Glossary Extraction.
Handles 1M Audio Pre-scan and Meeting Outline/Agenda Terminology Mining.
"""

import re
import time
from pathlib import Path
from google import genai
from scripts.audio_utils import safe_ascii_upload_path


def extract_keywords_from_glossary(glossary_text: str, max_keywords: int = 40) -> str:
    """Extract top entity keywords from glossary text to formulate a prompt for Whisper."""
    if not glossary_text:
        return ""
    
    extracted = []
    for line in glossary_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        # Match bold items like **黃偉哲** or **動保處**
        bolds = re.findall(r"\*\*([^*]+)\*\*", line)
        for b in bolds:
            b_clean = re.sub(r"[\(（].*?[\)）]", "", b).strip()
            if 2 <= len(b_clean) <= 12 and not any(x in b_clean for x in ["長官", "姓名", "機關", "專有名詞", "議題", "備註"]):
                if b_clean not in extracted:
                    extracted.append(b_clean)
        
        # Match table rows: | 名稱 | ...
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line[1:-1].split("|")]
            for p in parts:
                p_clean = re.sub(r"[\(（].*?[\)）]", "", p).replace("*", "").strip()
                if 2 <= len(p_clean) <= 10 and not any(x in p_clean for x in [":---", "---", "名稱", "職稱", "角色", "備註", "代號"]):
                    if p_clean not in extracted:
                        extracted.append(p_clean)

    top_terms = extracted[:max_keywords]
    if not top_terms:
        return ""
    return "，".join(top_terms) + "。"


def extract_global_consistency_glossary(
    client: genai.Client,
    audio_path: Path,
    outline_path: str | None = None,
    model: str = "gemini-3.7-flash",
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
    glossary_file = audio_path.parent / f"glossary_{audio_path.stem}.md"
    alt_glossary = audio_path.parent / f"{audio_path.stem}_glossary.md"
    target_cache = glossary_file if glossary_file.exists() else alt_glossary
    
    if not force and target_cache.exists():
        print(f"[*] ⚡ 發現已快取的專有名詞對照表: {target_cache.name}，直接載入！")
        cached_content = target_cache.read_text(encoding="utf-8")
        keywords = extract_keywords_from_glossary(cached_content)
        return cached_content, keywords

    print(f"\n========================================================")
    print(f"📚 [詞彙對齊] 啟動雙軌專有名詞探勘 (Global Consistency Glossary)")
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
    uploaded_file = None
    glossary_content = ""

    try:
        if compress_fn:
            compressed_audio = compress_fn(audio_path, bitrate="48k")
        else:
            compressed_audio = audio_path
            
        with safe_ascii_upload_path(compressed_audio) as safe_upload_path:
            print(f"[*] Uploading lightweight audio to Gemini 1M context for entity discovery ({safe_upload_path.stat().st_size / (1024*1024):.1f} MB)...")
            uploaded_file = client.files.upload(file=str(safe_upload_path))
        
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1.5)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            raise RuntimeError(f"Audio file upload processing failed: {uploaded_file.error}")

        response = client.models.generate_content(
            model=model,
            contents=[uploaded_file, prompt],
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
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if compressed_audio and compressed_audio != audio_path and compressed_audio.exists():
            compressed_audio.unlink()

    keywords = extract_keywords_from_glossary(glossary_content)
    return glossary_content, keywords
