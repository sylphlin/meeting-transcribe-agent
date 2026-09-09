"""
scripts/canonicalizer.py - Canonical Speaker ID Consolidation & Over-Clustering Unification.

Solves the multi-speaker fragment problem where the same person (e.g. the Mayor)
is split into multiple acoustic clusters (e.g. spk_2, spk_3, spk_7, spk_28) due to
acoustic cluster drift over long meetings.
"""

import re
from typing import Dict, List, Tuple


def parse_speaker_mapping_from_markdown(markdown_text: str) -> Dict[str, str]:
    """
    Parses the speaker mapping table from the generated meeting minutes.
    Supports multilingual table formats across English, Chinese, and Japanese:
    | Speaker ID | Role / Title | Name | Organization / Notes |
    | `spk_1, spk_3` | Chair / Host | John Doe | Executive Team |
    | spk_8 | VP of Engineering | Alice | Tech Dept |
    | spk_5 | Facilitator | — | — |
    """
    mapping = {}
    lines = markdown_text.splitlines()
    in_table = False

    table_triggers = [
        "speaker mapping", "attendees", "speakers", "participants", "speaker list",
        "與會首長及人員", "發言人對照表", "發言人對應表", "與會人員", "出席人員",
        "與會者", "角色對照", "出席者", "話者一覧", "登壇者"
    ]
    skip_header_tokens = [
        "speaker", "id", "代號", "說話者", "發言人", "name", "姓名", "role", "職稱", ":---"
    ]

    table_row_pattern = re.compile(r'^\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|(.*)$')
    spk_pattern = re.compile(r'spk_(\d+)', re.IGNORECASE)

    for line in lines:
        line_strip = line.strip()
        line_lower = line_strip.lower()

        if any(trig in line_lower for trig in table_triggers):
            in_table = True
            continue

        if in_table:
            if line_strip.startswith("---") or (line_strip.startswith("#") and not line_strip.startswith("###")):
                break

            m = table_row_pattern.match(line_strip)
            if m:
                col1 = m.group(1).strip()
                col2 = m.group(2).strip()
                col3 = m.group(3).strip()

                # Skip table header row
                col1_lower = col1.lower()
                if any(token in col1_lower for token in skip_header_tokens):
                    continue

                spk_matches = spk_pattern.findall(col1)
                if not spk_matches:
                    continue

                # Construct canonical display name
                title = col2.replace("*", "").replace("`", "").strip()
                name = col3.replace("*", "").replace("`", "").strip()
                if name in ["—", "-", "", "無", "None", "N/A"]:
                    canonical_name = title
                elif name in title:
                    canonical_name = title
                elif not title:
                    canonical_name = name
                else:
                    canonical_name = f"{title}（{name}）"

                for s_id in spk_matches:
                    mapping[f"spk_{s_id}"] = canonical_name

    return mapping


def consolidate_verbatim_transcript(transcript_text: str, speaker_mapping: Dict[str, str] = None) -> str:
    """
    Normalizes speaker names in transcript and merges consecutive turns from the same speaker.
    Handles turn format: `[MM:SS - MM:SS] **Speaker**: Content`
    """
    turn_pattern = re.compile(r'^\[(\d+:\d+(?::\d+)?)\s*-\s*(\d+:\d+(?::\d+)?)\]\s*\*\*([^*]+)\*\*[:：]\s*(.*)$')
    lines = transcript_text.splitlines()
    
    parsed_turns: List[dict] = []
    other_lines: List[Tuple[int, str]] = []  # Keep headings / non-turn lines with relative index

    mapping = speaker_mapping or {}

    for idx, line in enumerate(lines):
        line_str = line.strip()
        if not line_str:
            continue
        m = turn_pattern.match(line_str)
        if m:
            start_str, end_str, speaker_raw, content = m.groups()
            
            # Canonicalize speaker if matching spk_X
            speaker_clean = speaker_raw.strip()
            for spk_key, canonical_val in mapping.items():
                if spk_key in speaker_clean:
                    speaker_clean = speaker_clean.replace(spk_key, canonical_val)
            
            parsed_turns.append({
                "start": start_str,
                "end": end_str,
                "speaker": speaker_clean,
                "content": content.strip()
            })
        else:
            other_lines.append((len(parsed_turns), line_str))

    if not parsed_turns:
        return transcript_text

    # Merge consecutive turns from the same canonical speaker if time gap <= 2.0 seconds
    def to_sec(ts: str) -> float:
        parts = [float(p) for p in ts.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return parts[0] * 60 + parts[1]

    merged_turns: List[dict] = []
    for turn in parsed_turns:
        if not merged_turns:
            merged_turns.append(turn)
            continue
        
        last = merged_turns[-1]
        last_end_sec = to_sec(last["end"])
        cur_start_sec = to_sec(turn["start"])
        gap = cur_start_sec - last_end_sec

        # Check if same speaker and small gap (not overlapping speech)
        is_same_spk = (last["speaker"] == turn["speaker"])
        overlap_markers = ["同時", "重疊", "simultaneous", "overlap", "同時発言", "重複"]
        is_overlap = any(
            m in last["speaker"].lower() or m in turn["speaker"].lower()
            for m in overlap_markers
        )

        if is_same_spk and not is_overlap and 0.0 <= gap <= 2.0:
            # Merge text seamlessly
            sep = "" if last["content"] and last["content"][-1] in "，。！？；、：…" else " "
            last["content"] += sep + turn["content"]
            last["end"] = turn["end"]
        else:
            merged_turns.append(turn)

    # Reconstruct transcript markdown
    out_lines = []
    other_idx = 0
    total_others = len(other_lines)

    for i, t in enumerate(merged_turns):
        while other_idx < total_others and other_lines[other_idx][0] <= i:
            out_lines.append(other_lines[other_idx][1])
            out_lines.append("")
            other_idx += 1
        
        out_lines.append(f"[{t['start']} - {t['end']}] **{t['speaker']}**: {t['content']}")
        out_lines.append("")

    while other_idx < total_others:
        out_lines.append(other_lines[other_idx][1])
        out_lines.append("")
        other_idx += 1

    return "\n".join(out_lines).strip()


def consolidate_meeting_minutes(markdown_content: str) -> str:
    """
    Full pipeline canonicalization:
    1. Extracts speaker mapping table.
    2. Consolidates transcript section.
    3. Returns cleaned, unified Markdown document.
    """
    mapping = parse_speaker_mapping_from_markdown(markdown_content)
    
    # Split into summary vs transcript (multilingual support)
    split_keys = [
        "## 6. 🎙️ full verbatim transcript",
        "## 6. full verbatim transcript",
        "## 6. 🎙️ 完整時間戳記逐字稿",
        "## 6. 🎙️ 完整逐字稿",
        "### 6. 完整時間戳記逐字稿",
        "🎙️ 完整時間戳記逐字稿",
        "full verbatim transcript",
        "verbatim transcript",
        "完整時間戳記逐字稿",
        "文字起こし",
        "transcript"
    ]
    md_lower = markdown_content.lower()
    found_idx = -1
    for k in split_keys:
        idx = md_lower.find(k)
        if idx != -1:
            found_idx = idx
            break

    if found_idx == -1:
        return markdown_content

    line_start = markdown_content.rfind("\n", 0, found_idx)
    line_start = 0 if line_start == -1 else line_start + 1
    
    summary_part = markdown_content[:line_start].rstrip()
    header_and_transcript = markdown_content[line_start:]
    
    # Extract header line of Section 6
    lines = header_and_transcript.splitlines()
    sec6_header = lines[0]
    raw_transcript = "\n".join(lines[1:])

    clean_transcript = consolidate_verbatim_transcript(raw_transcript, mapping)

    return f"{summary_part}\n\n{sec6_header}\n\n{clean_transcript}\n"
