"""
scripts/canonicalizer.py - Canonical Speaker Identity Consolidation & Hierarchical Alignment.

Maps Stage 1 speaker labels (e.g. Speaker 1, Speaker 2 from Chirp 3 or spk_0, spk_1 from Whisper)
to real participant names and roles via 3-level hierarchical resolution (SRT Ground Truth ->
Time-Scoped Multimodal Rules -> Forward Conversational Handover Calibration).
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


# Subtitle cue: optional index line, timestamps (SRT `HH:MM:SS,mmm` or WebVTT `[HH:]MM:SS.mmm`),
# optional cue settings after the end time, then the payload up to the next blank line.
_SUBTITLE_CUE_RE = re.compile(
    r'(?:^|\n)[ \t]*(?:\d+[ \t]*\n)?'
    r'((?:\d{1,2}:)?\d{2}:\d{2}[,\.]\d{3})[ \t]*-->[ \t]*((?:\d{1,2}:)?\d{2}:\d{2}[,\.]\d{3})[^\n]*\n'
    r'(.*?)(?=\n[ \t]*\n|\Z)',
    re.DOTALL
)
# Layer 1: W3C WebVTT voice tag `<v Name>text</v>` or `<v.class Name>text`.
_VTT_VOICE_RE = re.compile(r'^\s*<v(?:\.[^\s>]+)?\s+([^>]+)>(.*)$', re.DOTALL)
# Layer 2: bracketed token `(Name)` or `[Name]` on its own line, before a colon, or before text.
# One nested level is allowed for pronoun or device suffixes: `(Jane Smith (she/her))`.
_BRACKET_SPEAKER_RE = re.compile(
    r'^\s*(?:\(((?:[^()\n]|\([^()\n]*\))+)\)|\[((?:[^\[\]\n]|\[[^\[\]\n]*\])+)\])[ \t]*(?:[:：]|\n|[ \t]|$)\s*(.*)$',
    re.DOTALL
)
# Layer 3: colon-delimited token `Name: text` or `Name：text` on the first line.
_COLON_SPEAKER_RE = re.compile(r'^\s*([^\n:：]{1,60}?)\s*[:：]\s*(.*)$', re.DOTALL)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_DIALOGUE_PREFIX_RE = re.compile(r'^\s*(?:>>|-)\s*')


def _extract_speaker_candidate(payload: str) -> Tuple[Optional[str], str]:
    """
    Extract a lenient speaker candidate token and the remaining dialogue text from one cue payload.
    Return (None, dialogue) when the cue has no speaker token.
    Plausibility is decided later by the speaker resolver, not here.
    """
    raw = (payload or "").strip()
    if not raw:
        return None, ""

    m_vtt = _VTT_VOICE_RE.match(raw)
    if m_vtt:
        token = m_vtt.group(1).strip()
        dialogue = _HTML_TAG_RE.sub('', m_vtt.group(2)).strip()
        return token or None, dialogue

    clean = _HTML_TAG_RE.sub('', raw).strip()
    clean = _DIALOGUE_PREFIX_RE.sub('', clean).strip()
    if not clean:
        return None, ""

    m_bracket = _BRACKET_SPEAKER_RE.match(clean)
    if m_bracket:
        token = (m_bracket.group(1) or m_bracket.group(2) or "").strip()
        # An empty tag such as `[ ]:` or `():` marks an unknown speaker. Return "" so the
        # caller skips the cue and does not inherit the previous speaker.
        return token, (m_bracket.group(3) or "").strip()

    m_colon = _COLON_SPEAKER_RE.match(clean)
    if m_colon:
        token = m_colon.group(1).strip()
        return token or None, (m_colon.group(2) or "").strip()

    return None, clean


def parse_srt_timeline(
    srt_content_or_path: str | Path,
    speaker_resolver=None,
    inherit_previous_speaker: bool = True,
    inherit_max_gap_sec: float = 2.0,
) -> List[dict]:
    """
    Parse an SRT or WebVTT subtitle track into chronological speaker events:
    [{"start": float, "end": float, "name": str}, ...]

    Extraction is a two-step process:
    1. Deterministic cue parsing with three lenient layers:
       - WebVTT voice tags `<v Name>` (Microsoft Teams, W3C)
       - Bracketed tokens `(Name)` / `[Name]` on their own line or before text (Google Meet, broadcast)
       - Colon-delimited tokens `Name:` / `Name：` (Zoom, Webex, Otter.ai, WhisperX)
    2. Candidate resolution on the distinct token set:
       - `speaker_resolver(candidates)` maps token -> canonical name or None (not a speaker).
       - When no resolver is given, the rule-based guardrail from `srt_speaker_resolver` is used.

    When `inherit_previous_speaker` is True, a cue without a speaker token takes the previous
    speaker when it starts within `inherit_max_gap_sec` of the previous cue end. Google Meet
    shows the name only when the speaker changes, so this keeps continuous turns covered.
    """
    from scripts.srt_speaker_resolver import is_hard_rejected_token, resolve_with_rules

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
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cues: List[dict] = []
    candidates: Dict[str, List[str]] = {}
    for match in _SUBTITLE_CUE_RE.finditer(text):
        start_str, end_str, payload = match.groups()
        token, dialogue = _extract_speaker_candidate(payload)
        explicit_unknown = token == ""
        if token:
            token = re.sub(r'[*`_]', '', token).strip()
            if is_hard_rejected_token(token):
                token = None
                explicit_unknown = True
        cue = {
            "start": parse_timestamp_to_seconds(start_str),
            "end": parse_timestamp_to_seconds(end_str),
            "token": token or None,
            "dialogue": dialogue,
            "explicit_unknown": explicit_unknown,
        }
        cues.append(cue)
        if token:
            candidates.setdefault(token, [])
            if dialogue and len(candidates[token]) < 5:
                candidates[token].append(dialogue)

    if not cues:
        return []

    resolution: Dict[str, Optional[str]] = {}
    if candidates:
        if speaker_resolver is not None:
            try:
                resolution = dict(speaker_resolver(candidates) or {})
            except Exception:
                resolution = {}
        if not resolution:
            resolution = resolve_with_rules(candidates)

    events: List[dict] = []
    prev_name: Optional[str] = None
    prev_end: Optional[float] = None
    for cue in cues:
        name: Optional[str] = None
        token = cue["token"]
        if token:
            name = resolution.get(token)
            if name is None and token not in resolution:
                name = token
            if name is None:
                # Sound description or label: this cue has no speaker and breaks continuity.
                prev_name = None
                prev_end = None
                continue
        elif (
            inherit_previous_speaker
            and not cue["explicit_unknown"]
            and prev_name
            and prev_end is not None
            and cue["dialogue"]
            and 0.0 <= (cue["start"] - prev_end) <= inherit_max_gap_sec
        ):
            name = prev_name

        if not name:
            prev_name = None
            prev_end = None
            continue

        events.append({"start": cue["start"], "end": cue["end"], "name": name})
        prev_name = name
        prev_end = cue["end"]

    return events


def generate_auto_outline_from_srt(srt_path: Path, output_path: Path = None, speaker_resolver=None) -> Path:
    """
    Analyzes an SRT subtitle file to generate an auto-outline Markdown document
    containing confirmed attendee roster and macro speaker timeline.
    Strictly plain text (Zero-Emoji policy).
    `speaker_resolver` is forwarded to `parse_srt_timeline` for candidate classification.
    """
    events = parse_srt_timeline(srt_path, speaker_resolver=speaker_resolver)
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
    Parse speaker identity mapping rules structurally from the Section 1 table.

    Column contract (defined in `assets/prompts/minutes_prompt.md`): the LLM may localize the
    header text, but the column ORDER is fixed and language-independent:
      | Speaker ID | Time Range | Role / Title | Name | Organization | Remarks |

    Detection is structural, not linguistic:
      - Speaker ID column: the first column whose data cells contain `Speaker N` / `spk_N`.
      - Time Range column: the first other column that holds a time range; when no column holds
        one, the contract position right after Speaker ID is used if it is empty (`-`).
        When neither applies, the table has no time column.
      - Role, then Name: the next two columns in order after excluding the two above.
    No per-language header keyword lists are used.

    Returns a list of scoped rule dicts:
      [{"spk_id": "spk_0", "start": 0.0, "end": 4.0, "name": "Alice Smith (Host)", "raw_name": "Alice Smith", "role": "Host"}, ...]
    """
    scoped_rules: List[dict] = []
    spk_pattern = re.compile(r'\b(?:spk[_\s-]*|speaker\s*)(\d+|[a-zA-Z])\b', re.IGNORECASE)
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    time_range_pattern = re.compile(r'(\d+:\d+(?::\d+)?)\s*(?:-|–|—|to)\s*(\d+:\d+(?::\d+)?)', re.IGNORECASE)

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

        start_row = 1
        if len(table_rows) > 1 and all(re.match(r'^:?-+:?$', _clean_cell(c) or "-") for c in table_rows[1]):
            start_row = 2
        data_rows = [r for r in table_rows[start_row:] if len(r) >= 2]
        if not data_rows:
            continue
        width = max(len(r) for r in data_rows)

        def _column_has(pattern: "re.Pattern[str]", c_idx: int) -> bool:
            return any(c_idx < len(r) and pattern.search(r[c_idx]) for r in data_rows)

        col_id = next((c for c in range(width) if _column_has(spk_pattern, c)), -1)
        if col_id < 0:
            continue
        # Time Range column: the first column that holds at least one time range. Cells without a
        # range in that column mean "whole meeting". When all speakers are unique the whole column
        # is `-`, so accept the contract position right after Speaker ID when it is empty.
        col_time = next((c for c in range(width) if c != col_id and _column_has(time_range_pattern, c)), -1)
        if col_time < 0 and col_id + 1 < width:
            if all(col_id + 1 >= len(r) or not _clean_cell(r[col_id + 1]) for r in data_rows):
                col_time = col_id + 1
        remaining = [c for c in range(width) if c not in (col_id, col_time)]
        col_role = remaining[0] if len(remaining) >= 1 else -1
        col_name = remaining[1] if len(remaining) >= 2 else -1
        if col_name < 0:
            # Single descriptor column: treat it as the name.
            col_name, col_role = col_role, -1

        for row in data_rows:
            if col_id >= len(row):
                continue
            spk_matches = spk_pattern.findall(row[col_id])
            if not spk_matches:
                continue

            start_sec = 0.0
            end_sec = float('inf')
            if 0 <= col_time < len(row):
                tm = time_range_pattern.search(row[col_time])
                if tm:
                    start_sec = parse_timestamp_to_seconds(tm.group(1))
                    end_sec = parse_timestamp_to_seconds(tm.group(2))

            # An empty Name cell (`-`) means the name is unknown: the role alone becomes the label.
            name_val = _clean_cell(row[col_name]) if 0 <= col_name < len(row) else ""
            role_val = _clean_cell(row[col_role]) if 0 <= col_role < len(row) else ""

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


ENTITY_CORRECTIONS_START = "<!-- ENTITY_CORRECTIONS_START -->"
ENTITY_CORRECTIONS_END = "<!-- ENTITY_CORRECTIONS_END -->"
_ENTITY_CORRECTIONS_BLOCK_RE = re.compile(
    re.escape(ENTITY_CORRECTIONS_START) + r'.*?' + re.escape(ENTITY_CORRECTIONS_END) + r'[ \t]*\n?',
    re.DOTALL
)


def _parse_corrections_table_by_position(block_text: str) -> Dict[str, str]:
    """
    Read the corrections table inside the marker block by column position:
    column 0 = mistranscribed term, column 1 = corrected term. Header text is ignored.
    """
    corrections: Dict[str, str] = {}
    table_row_pattern = re.compile(r'^\s*\|(.+)\|\s*$')
    rows = []
    for line in block_text.splitlines():
        m = table_row_pattern.match(line.strip())
        if m:
            rows.append([c.strip() for c in m.group(1).split("|")])
    for row in rows[1:]:
        if len(row) < 2:
            continue
        if all(re.match(r'^:?-+:?$', _clean_cell(c) or "-") for c in row):
            continue
        err_val = _clean_cell(row[0])
        fix_val = _clean_cell(row[1])
        if err_val and fix_val and err_val.lower() != fix_val.lower():
            corrections[err_val] = fix_val
    return corrections


def strip_entity_corrections_block(markdown_text: str) -> str:
    """
    Remove the internal entity corrections block (markers, label, and table) from the
    deliverable. The corrections are already applied to the transcript before this step.
    """
    if ENTITY_CORRECTIONS_START not in markdown_text:
        return markdown_text
    cleaned = _ENTITY_CORRECTIONS_BLOCK_RE.sub("", markdown_text)
    return re.sub(r'\n{3,}', '\n\n', cleaned)


def parse_entity_corrections_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parse phonetic slips and mistranscribed entity corrections generated in Stage 2.
    Primary path: the marker block `<!-- ENTITY_CORRECTIONS_START --> ... <!-- ENTITY_CORRECTIONS_END -->`
    read by column position. Legacy path (documents without markers): header keyword detection.
    Returns mapping from mistranscribed term to corrected term.
    """
    marker_match = _ENTITY_CORRECTIONS_BLOCK_RE.search(markdown_text)
    if marker_match:
        return _parse_corrections_table_by_position(marker_match.group(0))

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

    # Lookup: bare name (lowercase) -> "Name (Role)" from Stage 2 rules and mapping.
    # Subtitle events carry only the name, so Level 1 results are enriched with the role.
    name_to_full: Dict[str, str] = {}
    for r in rules:
        full_n = (r.get("name") or "").strip()
        raw_n = (r.get("raw_name") or re.sub(r'\(.*?\)', '', full_n)).strip()
        if raw_n and full_n and raw_n.lower() not in name_to_full:
            name_to_full[raw_n.lower()] = full_n
    for spk_val in mapping.values():
        full_n = (spk_val or "").strip()
        raw_n = re.sub(r'\(.*?\)', '', full_n).strip()
        if raw_n and full_n and raw_n.lower() not in name_to_full:
            name_to_full[raw_n.lower()] = full_n

    def _with_role(bare_name: str) -> str:
        full = name_to_full.get(bare_name.strip().lower())
        return full if full else bare_name

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
                    resolved_speaker = _with_role(best_ev["name"])
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
                        resolved_speaker = _with_role(closest_ev["name"])

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

            if not next_spk or re.match(r'^(?:spk[_\s-]*|speaker\s*)\d+', next_spk, re.IGNORECASE):
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


def consolidate_meeting_minutes(
    markdown_content: str,
    srt_path: Path | str = None,
    speaker_resolver=None,
) -> str:
    """
    Full pipeline canonicalization:
    1. Extracts scoped speaker mapping rules structurally.
    2. Extracts phonetic entity corrections table structurally.
    3. Optionally loads subtitle timeline events from SRT if available
       (`speaker_resolver` classifies subtitle speaker candidates).
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
        timeline_events = parse_srt_timeline(srt_path, speaker_resolver=speaker_resolver)

    # The corrections block is internal: apply it, then remove it from the deliverable.
    summary_part = strip_entity_corrections_block(summary_part or "")
    if not transcript_body:
        return strip_entity_corrections_block(markdown_content)

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
