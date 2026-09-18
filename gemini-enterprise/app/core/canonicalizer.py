"""
scripts/canonicalizer.py - Canonical Speaker ID Consolidation & Over-Clustering Unification.

Solves the multi-speaker fragment problem where the same person (e.g. Host, Chair, or Key Speaker)
is split into multiple acoustic clusters (e.g. spk_2, spk_3, spk_7, spk_28) due to
acoustic cluster drift over long meetings.
"""

import re
from typing import Dict, List, Tuple


def _clean_cell(cell: str) -> str:
    clean_val = re.sub(r'[*`_]', '', cell).strip()
    placeholder_tokens = {"-", "—", "--", "---", ":---", "none", "n/a", "null", "nil", ""}
    if clean_val.lower() in placeholder_tokens or re.match(r'^:?-+:?$', clean_val):
        return ""
    return clean_val


def parse_speaker_mapping_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses speaker identity mappings structurally from any markdown table containing `spk_\\d+` or `Speaker X`.
    Language-agnostic with header-aware semantic column detection:
    - Recognizes Name, Role/Title, and Organization columns.
    - Formats canonical badge as `Name (Role)` (Format A) when both exist,
      or `Name` if only name exists, or `Role` if only role exists.
    """
    mapping = {}
    spk_pattern = re.compile(r'\b(?:spk[_\s-]*|speaker\s*)(\d+|[a-zA-Z])\b', re.IGNORECASE)
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')

    # Semantic keyword patterns for header detection across multiple languages
    re_name = re.compile(r'\b(?:name|speaker|attendee|participant|nom|nombre)\b|姓名|名字|名稱|氏名|名前', re.IGNORECASE)
    re_role = re.compile(r'\b(?:role|title|position|titre|cargo|rolle)\b|職稱|角色|職務|役職|役割', re.IGNORECASE)
    re_spk_id = re.compile(r'\b(?:id|spk|speaker\s*id)\b|話者|發言人|發言者|識別', re.IGNORECASE)

    lines = markdown_text.splitlines()
    i = 0
    num_lines = len(lines)

    while i < num_lines:
        line = lines[i].strip()
        m = table_row_pattern.match(line)
        if not m:
            i += 1
            continue

        # Found the start of a potential markdown table
        table_rows = []
        while i < num_lines:
            row_line = lines[i].strip()
            rm = table_row_pattern.match(row_line)
            if not rm:
                break
            cells = [c.strip() for c in rm.group(1).split("|")]
            table_rows.append(cells)
            i += 1

        if len(table_rows) < 2:
            continue

        # Inspect row 0 as header
        header_cells = table_rows[0]
        col_id = -1
        col_name = -1
        col_role = -1

        for c_idx, h_cell in enumerate(header_cells):
            h_clean = _clean_cell(h_cell)
            if re_spk_id.search(h_clean) and not re_name.search(h_clean):
                col_id = c_idx
            elif re_name.search(h_clean):
                col_name = c_idx
            elif re_role.search(h_clean):
                col_role = c_idx

        # If header didn't match col_id, search for spk_pattern in data rows
        start_row = 1
        if len(table_rows) > 1 and all(re.match(r'^:?-+:?$', _clean_cell(c) or "-") for c in table_rows[1]):
            start_row = 2

        for r_idx in range(start_row, len(table_rows)):
            row = table_rows[r_idx]
            if len(row) < 2:
                continue

            # Find cell containing speaker IDs
            spk_cell_idx = col_id if (col_id >= 0 and col_id < len(row) and spk_pattern.search(row[col_id])) else -1
            spk_matches = []
            if spk_cell_idx >= 0:
                spk_matches = spk_pattern.findall(row[spk_cell_idx])
            else:
                for c_idx, cell in enumerate(row):
                    matches = spk_pattern.findall(cell)
                    if matches:
                        spk_cell_idx = c_idx
                        spk_matches = matches
                        break

            if not spk_matches or spk_cell_idx == -1:
                continue

            name_val = _clean_cell(row[col_name]) if (col_name >= 0 and col_name < len(row)) else ""
            role_val = _clean_cell(row[col_role]) if (col_role >= 0 and col_role < len(row)) else ""

            # Fallback if header detection was incomplete: gather descriptors
            if not name_val and not role_val:
                descriptors = []
                for idx, cell in enumerate(row):
                    if idx == spk_cell_idx:
                        continue
                    clean = _clean_cell(cell)
                    if clean:
                        descriptors.append(clean)
                if len(descriptors) >= 2:
                    # Heuristic: usually col 1 is Role and col 2 is Name
                    role_val = descriptors[0]
                    name_val = descriptors[1]
                elif descriptors:
                    name_val = descriptors[0]

            # Construct canonical badge according to Format A: Name (Role)
            if name_val and role_val:
                if role_val.lower() == name_val.lower() or role_val.lower() in name_val.lower():
                    canonical_name = name_val
                elif name_val.lower() in role_val.lower():
                    canonical_name = role_val
                else:
                    canonical_name = f"{name_val} ({role_val})"
            elif name_val:
                canonical_name = name_val
            elif role_val:
                canonical_name = role_val
            else:
                continue

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


def parse_entity_corrections_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses phonetic slips and mistranscribed entity corrections structurally from
    any `Phonetic & Entity Corrections Table` generated in Stage 2.
    Returns mapping from mistranscribed term to corrected term.
    """
    corrections = {}
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    re_mistranscribed = re.compile(r'\b(?:mistranscrib|misheard|original|error|slip)\b|誤聽|錯字|原始', re.IGNORECASE)
    re_corrected = re.compile(r'\b(?:correct|replacement|fix|canonical|entity)\b|正確|校正|替換|實體', re.IGNORECASE)

    lines = markdown_text.splitlines()
    i = 0
    num_lines = len(lines)

    while i < num_lines:
        line = lines[i].strip()
        m = table_row_pattern.match(line)
        if not m:
            i += 1
            continue

        table_rows = []
        while i < num_lines:
            row_line = lines[i].strip()
            rm = table_row_pattern.match(row_line)
            if not rm:
                break
            cells = [c.strip() for c in rm.group(1).split("|")]
            table_rows.append(cells)
            i += 1

        if len(table_rows) < 2:
            continue

        header_cells = table_rows[0]
        col_err = -1
        col_fix = -1

        for c_idx, h_cell in enumerate(header_cells):
            h_clean = _clean_cell(h_cell)
            if re_mistranscribed.search(h_clean):
                col_err = c_idx
            elif re_corrected.search(h_clean) and not re.search(r'\b(?:speaker|context|target speaker)\b|對象|語境', h_clean, re.IGNORECASE):
                col_fix = c_idx

        if col_err == -1 or col_fix == -1:
            continue

        start_row = 1
        if len(table_rows) > 1 and all(re.match(r'^:?-+:?$', _clean_cell(c) or "-") for c in table_rows[1]):
            start_row = 2

        for r_idx in range(start_row, len(table_rows)):
            row = table_rows[r_idx]
            if len(row) <= max(col_err, col_fix):
                continue
            err_val = _clean_cell(row[col_err])
            fix_val = _clean_cell(row[col_fix])
            if err_val and fix_val and err_val.lower() != fix_val.lower():
                corrections[err_val] = fix_val

    return corrections


def consolidate_verbatim_transcript(
    transcript_text: str,
    speaker_mapping: Dict[str, str] = None,
    entity_corrections: Dict[str, str] = None,
) -> str:
    """
    Normalizes speaker names in transcript turns and applies cross-modal entity corrections.
    Each turn keeps its own original timestamp range and stays a separate line/turn;
    turns are never merged into one another, preserving per-turn timing granularity.
    """
    mapping = speaker_mapping or {}
    corrections = entity_corrections or {}
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

            content_clean = content.strip()

            # Apply phonetic entity corrections to content
            if corrections:
                for err_term, fix_term in sorted(corrections.items(), key=lambda kv: len(kv[0]), reverse=True):
                    if re.search(r'[a-zA-Z]', err_term):
                        content_clean = re.sub(r'\b' + re.escape(err_term) + r'\b', fix_term, content_clean, flags=re.IGNORECASE)
                    else:
                        content_clean = content_clean.replace(err_term, fix_term)

            parsed_turns.append({
                "start": start_str,
                "end": end_str,
                "speaker": speaker_clean,
                "content": content_clean
            })
        else:
            if line_strip.startswith("#") or line_strip.startswith("---") or not parsed_turns:
                other_lines.append((len(parsed_turns), line_str))
            else:
                parsed_turns[-1]["content"] += "\n\n" + line_strip

    if not parsed_turns:
        return transcript_text

    # Turn-handover heuristic defense:
    # If turn i hands over to turn i+1 ("over to you, [MisheardName]"), align with next speaker's first name
    known_first_names = set()
    for spk_val in mapping.values():
        clean_name = re.sub(r'\(.*?\)', '', spk_val).strip()
        parts = clean_name.split()
        if parts:
            known_first_names.add(parts[0].lower())

    handover_pattern = re.compile(r'\b(?:over to you|over to|hand over to|passing to|turn over to|交給|有請|請)\s*,?\s*([A-Za-z]+)\b', re.IGNORECASE)
    ignore_addressed = {"everyone", "everybody", "all", "folks", "team", "guys", "you", "again", "there", "now"}

    for i in range(len(parsed_turns) - 1):
        curr_turn = parsed_turns[i]
        next_turn = parsed_turns[i + 1]

        next_spk = next_turn.get("speaker", "")
        clean_next_name = re.sub(r'\(.*?\)', '', next_spk).strip()
        next_parts = clean_next_name.split()
        if not next_parts:
            continue
        next_first_name = next_parts[0]

        # Check if current turn ends with a handover phrase (take the last occurrence in the turn)
        matches = list(handover_pattern.finditer(curr_turn["content"]))
        if matches:
            ho_match = matches[-1]
            addressed_word = ho_match.group(1)
            if addressed_word.lower() in ignore_addressed:
                continue
            # If addressed word is not any attendee's first name, align to next speaker's first name
            if addressed_word.lower() not in known_first_names and addressed_word.lower() != next_first_name.lower():
                curr_turn["content"] = re.sub(
                    r'\b' + re.escape(addressed_word) + r'\b',
                    next_first_name,
                    curr_turn["content"],
                    flags=re.IGNORECASE
                )

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
    2. Extracts phonetic entity corrections table structurally.
    3. Identifies summary and transcript boundaries.
    4. Normalizes speaker IDs and replaces in-text misheard entities.
    5. Reconstructs unified markdown document.
    """
    mapping = parse_speaker_mapping_from_markdown(markdown_content)
    corrections = parse_entity_corrections_from_markdown(markdown_content)
    summary_part, header_line, transcript_body = find_transcript_boundary(markdown_content)

    if not transcript_body:
        return markdown_content

    clean_transcript = consolidate_verbatim_transcript(transcript_body, mapping, corrections)
    header_block = f"{header_line}\n\n" if header_line else ""

    if summary_part:
        return f"{summary_part}\n\n{header_block}{clean_transcript}\n"
    return f"{header_block}{clean_transcript}\n"

