"""
scripts/canonicalizer.py - Canonical Speaker ID Consolidation & Over-Clustering Unification.

Solves the multi-speaker fragment problem where the same person (e.g. Host, Chair, or Key Speaker)
is split into multiple acoustic clusters (e.g. spk_2, spk_3, spk_7, spk_28) due to
acoustic cluster drift over long meetings.
"""

import re
from typing import Dict, List, Tuple


def parse_speaker_mapping_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses speaker identity mappings structurally from any markdown table containing `spk_\\d+` or `Speaker X`.
    Language-agnostic: requires no hardcoded triggers or language-specific keywords.
    """
    mapping = {}
    spk_pattern = re.compile(r'\b(?:spk[_\s-]*|speaker\s*)(\d+|[a-zA-Z])\b', re.IGNORECASE)
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    placeholder_tokens = {"-", "—", "--", "---", ":---", "none", "n/a", "null", "nil", ""}

    for line in markdown_text.splitlines():
        line_strip = line.strip()
        m = table_row_pattern.match(line_strip)
        if not m:
            continue

        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2:
            continue

        # Check if any cell contains spk_X / Speaker X references
        spk_cell_idx = -1
        spk_matches = []
        for idx, cell in enumerate(cells):
            matches = spk_pattern.findall(cell)
            if matches:
                spk_cell_idx = idx
                spk_matches = matches
                break

        if spk_cell_idx == -1:
            continue

        # Extract non-empty descriptive fields from other cells in this row
        descriptors = []
        for idx, cell in enumerate(cells):
            if idx == spk_cell_idx:
                continue
            clean_val = re.sub(r'[*`_]', '', cell).strip()
            clean_lower = clean_val.lower()
            if clean_lower not in placeholder_tokens and not re.match(r'^:?-+:?$', clean_val):
                descriptors.append(clean_val)

        if not descriptors:
            continue

        # Construct canonical display name using primary descriptor (Name / Role)
        canonical_name = descriptors[0]

        for s_id in spk_matches:
            s_lower = s_id.lower()
            mapping[f"spk_{s_id}"] = canonical_name
            mapping[f"spk_{s_lower}"] = canonical_name
            mapping[f"spk-{s_id}"] = canonical_name
            mapping[f"spk-{s_lower}"] = canonical_name
            mapping[f"speaker {s_id}"] = canonical_name
            mapping[f"speaker {s_lower}"] = canonical_name
            mapping[f"speaker_{s_id}"] = canonical_name
            mapping[f"speaker_{s_lower}"] = canonical_name

    return mapping


def consolidate_verbatim_transcript(transcript_text: str, speaker_mapping: Dict[str, str] = None) -> str:
    """
    Normalizes speaker names in transcript turns.
    Each turn keeps its own original timestamp range and stays a separate line/turn;
    turns are never merged into one another, so per-turn timing granularity (used by
    the interactive player for click-to-seek) is never lost. Visually chaining
    consecutive same-speaker turns together is a presentation concern handled by the
    HTML/CSS player layer, not a data transformation here.
    """
    mapping = speaker_mapping or {}
    turn_regex = re.compile(r'^\s*\[(\d+:\d+(?::\d+)?)\s*-\s*(\d+:\d+(?::\d+)?)\]\s*(?:\*\*([^*]+)\*\*[:：])?\s*(.*)', re.DOTALL)

    parsed_turns: List[dict] = []
    other_lines: List[Tuple[int, str]] = []

    for line_str in transcript_text.splitlines():
        line_strip = line_str.strip()
        if not line_strip:
            continue

        match = turn_regex.match(line_strip)
        if match:
            start_str, end_str, speaker_str, content = match.groups()
            speaker_clean = (speaker_str or "").strip()

            # Canonicalize speaker if matching spk_X using word boundary and length-descending order
            for spk_key in sorted(mapping.keys(), key=len, reverse=True):
                canonical_val = mapping[spk_key]
                speaker_clean = re.sub(r'\b' + re.escape(spk_key) + r'\b', canonical_val, speaker_clean, flags=re.IGNORECASE)

            parsed_turns.append({
                "start": start_str,
                "end": end_str,
                "speaker": speaker_clean,
                "content": content.strip()
            })
        else:
            if line_strip.startswith("#") or line_strip.startswith("---") or not parsed_turns:
                other_lines.append((len(parsed_turns), line_str))
            else:
                parsed_turns[-1]["content"] += "\n\n" + line_strip

    if not parsed_turns:
        return transcript_text

    out_lines = []
    other_idx = 0
    total_others = len(other_lines)

    for i, t in enumerate(parsed_turns):
        while other_idx < total_others and other_lines[other_idx][0] <= i:
            out_lines.append(other_lines[other_idx][1])
            out_lines.append("")
            other_idx += 1

        spk_tag = f"**{t['speaker']}**: " if t['speaker'] else ""
        out_lines.append(f"[{t['start']} - {t['end']}] {spk_tag}{t['content']}")
        out_lines.append("")

    while other_idx < total_others:
        out_lines.append(other_lines[other_idx][1])
        out_lines.append("")
        other_idx += 1

    return "\n".join(out_lines).strip()


def find_transcript_boundary(markdown_content: str) -> Tuple[str, str, str]:
    """
    Structurally identifies the boundary between the summary portion and the verbatim transcript.
    Finds the first timestamped turn line, then looks backwards for the nearest markdown header (#).
    Returns (summary_part, section_header, transcript_part).
    """
    timestamp_turn_regex = re.compile(r'^\s*\[\d+:\d+(?::\d+)?\s*-\s*\d+:\d+(?::\d+)?\]', re.MULTILINE)
    match = timestamp_turn_regex.search(markdown_content)

    if not match:
        return markdown_content, "", ""

    first_turn_pos = match.start()
    before_turn = markdown_content[:first_turn_pos]

    # Find the nearest header before the first turn
    header_matches = list(re.finditer(r'^\s*(#+\s+[^\n]+)', before_turn, re.MULTILINE))

    if header_matches:
        last_header = header_matches[-1]
        header_start = last_header.start()
        header_line = last_header.group(1).strip()
        summary_part = markdown_content[:header_start].rstrip()
        transcript_body = markdown_content[last_header.end():].lstrip()
        return summary_part, header_line, transcript_body
    else:
        summary_part = markdown_content[:first_turn_pos].rstrip()
        transcript_body = markdown_content[first_turn_pos:].lstrip()
        return summary_part, "", transcript_body


def consolidate_meeting_minutes(markdown_content: str) -> str:
    """
    Full pipeline canonicalization:
    1. Extracts speaker mapping table structurally.
    2. Identifies summary and transcript boundaries.
    3. Normalizes speaker IDs and merges sequential turns.
    4. Reconstructs unified markdown document.
    """
    mapping = parse_speaker_mapping_from_markdown(markdown_content)
    summary_part, header_line, transcript_body = find_transcript_boundary(markdown_content)

    if not transcript_body:
        return markdown_content

    clean_transcript = consolidate_verbatim_transcript(transcript_body, mapping)
    header_block = f"{header_line}\n\n" if header_line else ""

    if summary_part:
        return f"{summary_part}\n\n{header_block}{clean_transcript}\n"
    return f"{header_block}{clean_transcript}\n"

