"""
scripts/canonicalizer.py - Canonical Speaker ID Consolidation & Over-Clustering Unification.

Solves the multi-speaker fragment problem where the same person (e.g. Host, Chair, or Key Speaker)
is split into multiple acoustic clusters (e.g. spk_2, spk_3, spk_7, spk_28) due to
acoustic cluster drift over long meetings.
"""

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def parse_timestamp_to_seconds(ts_str: str) -> float:
    """
    Convert timestamp string (MM:SS, HH:MM:SS, or with decimals / commas) to float seconds.
    Examples:
      '00:04' -> 4.0
      '07:35' -> 455.0
      '01:02:03' -> 3723.0
      '00:00:01,500' -> 1.5
    """
    if not ts_str:
        return 0.0
    cleaned = ts_str.strip().replace(",", ".")
    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        elif len(parts) == 1:
            return float(parts[0])
    except ValueError:
        pass
    return 0.0


def parse_srt_timeline(srt_content_or_path: str | Path) -> List[dict]:
    """
    Parses an SRT subtitle track into chronological speaker events:
    [{"start": float, "end": float, "name": str}, ...]
    
    Robustly handles:
    - WebRTC speaker tags: 'Name: dialogue', '(Name): dialogue', '[Name]: dialogue'
    - Empty speaker tags: '(): dialogue', '[]: dialogue' (ignored)
    - Embedded HTML/formatting tags (<font color="...">, <b>, <i>, etc.)
    - Subtitles without speaker tags (ignored to prevent mistaking dialogue for names)
    """
    text = ""
    if isinstance(srt_content_or_path, Path) or (isinstance(srt_content_or_path, str) and "\n" not in srt_content_or_path and Path(srt_content_or_path).is_file()):
        try:
            text = Path(srt_content_or_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
    else:
        text = str(srt_content_or_path or "")

    if not text.strip():
        return []

    # Match SRT cues: index, timestamps, and payload
    cue_pattern = re.compile(
        r'(?:^|\n)\s*\d+\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)',
        re.DOTALL
    )

    events: List[dict] = []
    speaker_prefix_pattern = re.compile(
        r'^\s*(?:\(([^()]+)\)|\[([^\[\]]+)\]|([^:：\n]+))\s*[:：]\s*(.*)$',
        re.DOTALL
    )
    invalid_name_tokens = {"-", "--", "none", "n/a", "null", "nil", "unknown", "()", "[]"}

    for match in cue_pattern.finditer(text):
        start_str, end_str, payload = match.groups()
        start_sec = parse_timestamp_to_seconds(start_str)
        end_sec = parse_timestamp_to_seconds(end_str)

        clean_payload = re.sub(r'<[^>]+>', '', payload).strip()
        if not clean_payload:
            continue

        spk_m = speaker_prefix_pattern.match(clean_payload)
        if not spk_m:
            continue

        candidate_name = (spk_m.group(1) or spk_m.group(2) or spk_m.group(3) or "").strip()
        candidate_name = re.sub(r'[*`_]', '', candidate_name).strip()

        if (
            not candidate_name
            or candidate_name.lower() in invalid_name_tokens
            or candidate_name.isdigit()
            or len(candidate_name) > 60
            or re.search(r'[.!?。！？]\s*$', candidate_name)
        ):
            continue

        events.append({
            "start": start_sec,
            "end": end_sec,
            "name": candidate_name
        })

    return events


def generate_auto_outline_from_srt(srt_path: Path, output_path: Path = None) -> Path:
    """
    Analyzes an SRT subtitle file to generate an auto-outline Markdown document
    containing confirmed attendee roster and macro speaker timeline.
    Strictly plain text (Zero-Emoji policy).
    """
    events = parse_srt_timeline(srt_path)
    if output_path is None:
        output_path = srt_path.parent / "auto_outline.md"
    else:
        output_path = Path(output_path).resolve()

    if not events:
        content = "# Official Meeting Reference Outline\n\n- No confirmed attendees extracted from subtitles.\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    attendee_order = []
    seen = set()
    speaker_intervals: Dict[str, List[Tuple[float, float]]] = {}

    for ev in events:
        name = ev["name"]
        if name not in seen:
            seen.add(name)
            attendee_order.append(name)
        if name not in speaker_intervals:
            speaker_intervals[name] = []
        speaker_intervals[name].append((ev["start"], ev["end"]))

    # Synthesize macro speaking ranges (merge contiguous or nearby segments <= 15s gap)
    timeline_lines = []
    for name in attendee_order:
        intervals = speaker_intervals[name]
        merged = []
        cur_start, cur_end = intervals[0]
        for s, e in intervals[1:]:
            if s <= cur_end + 15.0:
                cur_end = max(cur_end, e)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = s, e
        merged.append((cur_start, cur_end))

        for s, e in merged:
            dur = e - s
            if dur >= 3.0:
                from scripts.audio_utils import format_offset
                timeline_lines.append(f"- [{format_offset(s)} - {format_offset(e)}] {name}")

    attendee_lines = "\n".join(f"- {name}" for name in attendee_order)
    timeline_block = "\n".join(timeline_lines) if timeline_lines else "- Ongoing group discussion."

    outline_text = (
        f"# Official Meeting Reference Outline\n\n"
        f"## Confirmed Attendees (WebRTC Ground Truth)\n"
        f"{attendee_lines}\n\n"
        f"## Macro Discussion Timeline\n"
        f"{timeline_block}\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(outline_text, encoding="utf-8")
    return output_path


def _clean_cell(cell: str) -> str:
    clean_val = re.sub(r'[*`_]', '', cell).strip()
    placeholder_tokens = {"-", "—", "--", "---", ":---", "none", "n/a", "null", "nil", ""}
    if clean_val.lower() in placeholder_tokens or re.match(r'^:?-+:?$', clean_val):
        return ""
    return clean_val


def parse_scoped_speaker_mapping(markdown_text: str) -> List[dict]:
    """
    Parses speaker identity mapping rules structurally from markdown tables.
    Supports Time Range column for temporal scoping to eliminate acoustic under-clustering:
      | Speaker ID | Time Range (optional if unique) | Role / Title | Name | Organization / Department | Remarks |
    
    Returns a list of scoped rule dicts:
      [{"spk_id": "spk_0", "start": 0.0, "end": 4.0, "name": "Alice Smith (Host)", "raw_name": "Alice Smith", "role": "Host"}, ...]
    """
    scoped_rules: List[dict] = []
    spk_pattern = re.compile(r'\b(?:spk[_\s-]*|speaker\s*)(\d+|[a-zA-Z])\b', re.IGNORECASE)
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    time_range_pattern = re.compile(r'(\d+:\d+(?::\d+)?)\s*(?:-|–|—|to)\s*(\d+:\d+(?::\d+)?)', re.IGNORECASE)

    re_name = re.compile(r'\b(?:name|speaker|attendee|participant|nom|nombre)\b|姓名|名字|名稱|氏名|名前', re.IGNORECASE)
    re_role = re.compile(r'\b(?:role|title|position|titre|cargo|rolle)\b|職稱|角色|職務|役職|役割', re.IGNORECASE)
    re_spk_id = re.compile(r'\b(?:id|spk|speaker\s*id)\b|話者|發言人|發言者|識別', re.IGNORECASE)
    re_time = re.compile(r'\b(?:time|timerange|range|duration|timestamp)\b|時段|時間|時間軸|期間', re.IGNORECASE)

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
        col_id = -1
        col_time = -1
        col_name = -1
        col_role = -1

        for c_idx, h_cell in enumerate(header_cells):
            h_clean = _clean_cell(h_cell)
            if re_spk_id.search(h_clean) and not re_name.search(h_clean):
                col_id = c_idx
            elif re_time.search(h_clean):
                col_time = c_idx
            elif re_name.search(h_clean):
                col_name = c_idx
            elif re_role.search(h_clean):
                col_role = c_idx

        start_row = 1
        if len(table_rows) > 1 and all(re.match(r'^:?-+:?$', _clean_cell(c) or "-") for c in table_rows[1]):
            start_row = 2

        for r_idx in range(start_row, len(table_rows)):
            row = table_rows[r_idx]
            if len(row) < 2:
                continue

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

            # Time range detection
            start_sec = 0.0
            end_sec = float('inf')
            if col_time >= 0 and col_time < len(row):
                tm = time_range_pattern.search(row[col_time])
                if tm:
                    start_sec = parse_timestamp_to_seconds(tm.group(1))
                    end_sec = parse_timestamp_to_seconds(tm.group(2))
            else:
                for idx, cell in enumerate(row):
                    if idx == spk_cell_idx:
                        continue
                    tm = time_range_pattern.search(cell)
                    if tm:
                        start_sec = parse_timestamp_to_seconds(tm.group(1))
                        end_sec = parse_timestamp_to_seconds(tm.group(2))
                        break

            name_val = _clean_cell(row[col_name]) if (col_name >= 0 and col_name < len(row)) else ""
            role_val = _clean_cell(row[col_role]) if (col_role >= 0 and col_role < len(row)) else ""

            if not name_val and not role_val:
                descriptors = []
                for idx, cell in enumerate(row):
                    if idx == spk_cell_idx or idx == col_time:
                        continue
                    clean = _clean_cell(cell)
                    if clean and not time_range_pattern.search(clean):
                        descriptors.append(clean)
                if len(descriptors) >= 2:
                    role_val = descriptors[0]
                    name_val = descriptors[1]
                elif descriptors:
                    name_val = descriptors[0]

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
                scoped_rules.append({
                    "spk_id": f"spk_{s_id.lower()}",
                    "start": start_sec,
                    "end": end_sec,
                    "name": canonical_name,
                    "raw_name": name_val,
                    "role": role_val
                })

    return scoped_rules


def parse_speaker_mapping_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses speaker identity mappings structurally from any markdown table containing `spk_\\d+` or `Speaker X`.
    Returns Dict[str, str] for backward compatibility.
    """
    rules = parse_scoped_speaker_mapping(markdown_text)
    mapping = {}
    for r in rules:
        s_id = r["spk_id"]  # e.g. "spk_0"
        clean_num = s_id.replace("spk_", "")
        canonical_name = r["name"]
        mapping[s_id] = canonical_name
        mapping[f"spk-{clean_num}"] = canonical_name
        mapping[f"speaker {clean_num}"] = canonical_name
        mapping[f"speaker_{clean_num}"] = canonical_name
    return mapping


def parse_entity_corrections_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses phonetic slips and mistranscribed entity corrections structurally from
    any `Phonetic & Entity Corrections Table` generated in Stage 2.
    Returns mapping from mistranscribed term to corrected term.
    """
    corrections = {}
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    re_mistranscribed = re.compile(r'\b(?:mistranscrib\w*|misheard|original|error|slip)\b|誤聽|錯字|原始', re.IGNORECASE)
    re_corrected = re.compile(r'\b(?:correct\w*|replacement|fix|canonical|entity)\b|正確|校正|替換|實體', re.IGNORECASE)

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
    timeline_events: List[dict] = None,
    scoped_rules: List[dict] = None,
) -> str:
    """
    Normalizes speaker names in transcript turns using hierarchical multi-modal alignment:
      Level 1: Subtitle Ground Truth (Temporal overlap from embedded WebRTC captions)
      Level 2: Multimodal Scoped Rules (Visual time-bounded mapping from Stage 2)
      Level 3: Forward Handover Calibration (Conversational handoffs across turns)
    Preserves acoustic clock timestamps and turn granularity without merging turns.
    """
    mapping = speaker_mapping or {}
    corrections = entity_corrections or {}
    events = timeline_events or []
    rules = scoped_rules or []

    turn_regex = re.compile(
        r'^\s*\[(\d+:\d+(?::\d+)?)\s*-\s*(\d+:\d+(?::\d+)?)\]\s*(?:\*\*([^*]+)\*\*[:：])?\s*(.*)',
        re.DOTALL
    )

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
            turn_start_sec = parse_timestamp_to_seconds(start_str)
            turn_end_sec = parse_timestamp_to_seconds(end_str)
            turn_dur = max(0.1, turn_end_sec - turn_start_sec)
            turn_center_sec = (turn_start_sec + turn_end_sec) / 2.0

            resolved_speaker: Optional[str] = None

            # ----------------------------------------------------
            # Level 1: Subtitle Ground Truth (Temporal Overlap)
            # ----------------------------------------------------
            if events:
                best_ev = None
                max_overlap = 0.0
                for ev in events:
                    ev_name = ev.get("name", "").strip()
                    if not ev_name:
                        continue
                    overlap = max(0.0, min(turn_end_sec, ev["end"]) - max(turn_start_sec, ev["start"]))
                    if overlap > max_overlap:
                        max_overlap = overlap
                        best_ev = ev

                if best_ev and max_overlap > 0.0:
                    resolved_speaker = best_ev["name"]
                elif turn_dur <= 2.5:
                    # Tolerance radius for brief interjections / short turns
                    closest_ev = None
                    min_dist = float('inf')
                    for ev in events:
                        ev_name = ev.get("name", "").strip()
                        if not ev_name:
                            continue
                        ev_center = (ev["start"] + ev["end"]) / 2.0
                        dist = abs(turn_center_sec - ev_center)
                        if dist <= 3.0 and dist < min_dist:
                            min_dist = dist
                            closest_ev = ev
                    if closest_ev:
                        resolved_speaker = closest_ev["name"]

            # ----------------------------------------------------
            # Level 2: Multimodal Scoped Rules (Visual / Time-Bounded)
            # ----------------------------------------------------
            if not resolved_speaker and rules:
                m_spk = re.search(r'\b(?:spk[_\s-]*|speaker\s*)(\d+|[a-zA-Z])\b', speaker_clean, re.IGNORECASE)
                if m_spk:
                    s_id_norm = f"spk_{m_spk.group(1).lower()}"
                    matching_rules = [r for r in rules if r["spk_id"] == s_id_norm]
                    if matching_rules:
                        if len(matching_rules) == 1 and matching_rules[0]["end"] == float('inf'):
                            resolved_speaker = matching_rules[0]["name"]
                        else:
                            # Evaluate temporal overlap with +-30s padding buffer
                            best_rule = None
                            max_rule_overlap = -1.0
                            for r in matching_rules:
                                r_start = max(0.0, r["start"] - 30.0)
                                r_end = r["end"] + 30.0 if r["end"] != float('inf') else float('inf')
                                overlap = max(0.0, min(turn_end_sec, r_end) - max(turn_start_sec, r_start))
                                if overlap > max_rule_overlap and overlap > 0.0:
                                    max_rule_overlap = overlap
                                    best_rule = r

                            if best_rule:
                                resolved_speaker = best_rule["name"]
                            else:
                                min_dist = float('inf')
                                for r in matching_rules:
                                    r_center = (r["start"] + (r["end"] if r["end"] != float('inf') else r["start"])) / 2.0
                                    dist = abs(turn_center_sec - r_center)
                                    if dist < min_dist:
                                        min_dist = dist
                                        best_rule = r
                                if best_rule:
                                    resolved_speaker = best_rule["name"]

            # Fallback to dictionary mapping if neither Level 1 nor Level 2 resolved
            if resolved_speaker:
                speaker_clean = resolved_speaker
            elif mapping:
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

    # ----------------------------------------------------
    # Level 3: Forward Handover Calibration (Linguistic Handoffs)
    # ----------------------------------------------------
    known_attendees = {}
    for ev in events:
        n = ev.get("name", "").strip()
        if n:
            known_attendees[n.lower()] = n
            parts = n.split()
            if parts:
                known_attendees[parts[0].lower()] = n
    for r in rules:
        full_n = r.get("name", "").strip()
        n = r.get("raw_name") or full_n
        clean_n = re.sub(r'\(.*?\)', '', n).strip()
        if clean_n:
            known_attendees[clean_n.lower()] = full_n
            parts = clean_n.split()
            if parts:
                known_attendees[parts[0].lower()] = full_n
    for spk_val in mapping.values():
        full_n = spk_val.strip()
        clean_n = re.sub(r'\(.*?\)', '', full_n).strip()
        if clean_n:
            known_attendees[clean_n.lower()] = full_n
            parts = clean_n.split()
            if parts:
                known_attendees[parts[0].lower()] = full_n

    # Language-agnostic unified regex for conversational handover phrases
    handover_pattern = re.compile(
        r'\b(?:over to you|over to|hand over to|hand it over to|pass it to|pass to|passing to|turn over to|take it away|to you|交給|有請|交棒給|交由|請)\s*,?\s*([A-Za-z\u4e00-\u9fa5]{1,25})\b',
        re.IGNORECASE
    )
    ignore_addressed = {"everyone", "everybody", "all", "folks", "team", "guys", "you", "again", "there", "now", "各位", "大家", "老師"}

    for i in range(len(parsed_turns) - 1):
        curr_turn = parsed_turns[i]
        next_turn = parsed_turns[i + 1]

        matches = list(handover_pattern.finditer(curr_turn["content"]))
        if not matches:
            continue

        ho_match = matches[-1]
        addressed_word = ho_match.group(1).strip()
        if addressed_word.lower() in ignore_addressed:
            continue

        target_name = known_attendees.get(addressed_word.lower())
        if not target_name:
            for k, full_n in known_attendees.items():
                if addressed_word.lower() in k or k in addressed_word.lower():
                    target_name = full_n
                    break

        if target_name:
            target_clean = re.sub(r'\(.*?\)', '', target_name).strip().lower()
            next_spk = next_turn.get("speaker", "")
            next_clean = re.sub(r'\(.*?\)', '', next_spk).strip().lower()

            if not next_spk or re.match(r'^spk[_\s-]*\d+', next_spk, re.IGNORECASE):
                next_turn["speaker"] = target_name
            elif target_clean not in next_clean and next_clean not in target_clean:
                next_turn["speaker"] = target_name

            curr_spk = curr_turn.get("speaker", "")
            curr_clean = re.sub(r'\(.*?\)', '', curr_spk).strip().lower()
            if curr_clean and (curr_clean == target_clean or curr_clean in target_clean or target_clean in curr_clean):
                curr_turn["speaker"] = ""

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


def consolidate_meeting_minutes(markdown_content: str, srt_path: Path | str = None) -> str:
    """
    Full pipeline canonicalization:
    1. Extracts scoped speaker mapping rules structurally.
    2. Extracts phonetic entity corrections table structurally.
    3. Optionally loads subtitle timeline events from SRT if available.
    4. Identifies summary and transcript boundaries.
    5. Normalizes speaker IDs with hierarchical resolution (SRT -> Scoped -> Handover).
    6. Reconstructs unified markdown document.
    """
    scoped_rules = parse_scoped_speaker_mapping(markdown_content)
    mapping = parse_speaker_mapping_from_markdown(markdown_content)
    corrections = parse_entity_corrections_from_markdown(markdown_content)
    summary_part, header_line, transcript_body = find_transcript_boundary(markdown_content)

    timeline_events = []
    if srt_path:
        timeline_events = parse_srt_timeline(srt_path)

    if not transcript_body:
        return markdown_content

    clean_transcript = consolidate_verbatim_transcript(
        transcript_body,
        speaker_mapping=mapping,
        entity_corrections=corrections,
        timeline_events=timeline_events,
        scoped_rules=scoped_rules
    )
    header_block = f"{header_line}\n\n" if header_line else ""

    if summary_part:
        return f"{summary_part}\n\n{header_block}{clean_transcript}\n"
    return f"{header_block}{clean_transcript}\n"
