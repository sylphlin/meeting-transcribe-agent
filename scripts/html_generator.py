"""
scripts/html_generator.py - Standalone HTML Interactive Meeting Player Generator.
Decoupled view renderer loading templates from assets/player_template.html.
"""

import os
import re
import html
from pathlib import Path


def parse_timestamp_to_seconds(ts_str: str) -> float:
    """Convert timestamp string (HH:MM:SS or MM:SS) to floating-point seconds."""
    parts = ts_str.strip().split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def inline_md(text: str) -> str:
    """Escapes HTML and converts inline markdown (code, strong, em, link)."""
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    return text


def parse_markdown_to_html(md_text: str) -> str:
    """
    Lightweight, robust Markdown to HTML renderer:
    Supports Headings, Tables, Lists, Blockquotes, and Paragraphs.
    """
    lines = md_text.splitlines()
    html_out = []
    in_table = False
    table_rows = []
    alignments = []
    list_stack = []

    def close_lists_up_to(indent=-1):
        nonlocal list_stack, html_out
        while list_stack and (indent == -1 or list_stack[-1][1] > indent):
            tag, _ = list_stack.pop()
            html_out.append(f"</{tag}>")

    def flush_table():
        nonlocal in_table, table_rows, alignments, html_out
        if not table_rows:
            in_table = False
            return
        
        t_html = ['<div class="doc-table-wrapper"><table class="doc-table">']
        header_cells = table_rows[0]
        t_html.append('<thead><tr>')
        for idx, cell in enumerate(header_cells):
            align_attr = f' style="text-align:{alignments[idx]};"' if idx < len(alignments) and alignments[idx] else ''
            t_html.append(f'<th{align_attr}>{inline_md(cell)}</th>')
        t_html.append('</tr></thead><tbody>')

        for row in table_rows[1:]:
            t_html.append('<tr>')
            for idx, cell in enumerate(row):
                align_attr = f' style="text-align:{alignments[idx]};"' if idx < len(alignments) and alignments[idx] else ''
                t_html.append(f'<td{align_attr}>{inline_md(cell)}</td>')
            t_html.append('</tr>')

        t_html.append('</tbody></table></div>')
        html_out.append('\n'.join(t_html))
        table_rows = []
        alignments = []
        in_table = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Blank line
        if not stripped:
            if in_table:
                flush_table()
            close_lists_up_to(-1)
            i += 1
            continue

        # Horizontal Rule
        if stripped in ["---", "***", "___"]:
            if in_table:
                flush_table()
            close_lists_up_to(-1)
            html_out.append('<hr style="border:0; border-top:1px solid var(--border-color); margin:20px 0;">')
            i += 1
            continue

        # Markdown Table Detection
        if stripped.startswith("|") and stripped.endswith("|"):
            close_lists_up_to(-1)
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            
            # Check separator row
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                alignments = []
                for c in cells:
                    left = c.startswith(":")
                    right = c.endswith(":")
                    if left and right:
                        alignments.append("center")
                    elif right:
                        alignments.append("right")
                    elif left:
                        alignments.append("left")
                    else:
                        alignments.append("left")
                i += 1
                continue
            
            in_table = True
            table_rows.append(cells)
            i += 1
            continue
        elif in_table:
            flush_table()

        # Headings
        m_h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m_h:
            close_lists_up_to(-1)
            level = len(m_h.group(1))
            h_text = inline_md(m_h.group(2))
            tag_level = min(level + 1, 6)
            html_out.append(f'<h{tag_level} class="doc-h{tag_level}">{h_text}</h{tag_level}>')
            i += 1
            continue

        # Blockquote
        if stripped.startswith(">"):
            close_lists_up_to(-1)
            b_text = inline_md(stripped.lstrip("> ").strip())
            html_out.append(f'<blockquote class="doc-quote">{b_text}</blockquote>')
            i += 1
            continue

        # Lists (unordered and ordered)
        m_ul = re.match(r"^(\s*)[-\*]\s+(.*)$", line)
        m_ol = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m_ul or m_ol:
            is_ol = bool(m_ol)
            indent = len(m_ol.group(1) if is_ol else m_ul.group(1))
            content = inline_md(m_ol.group(3) if is_ol else m_ul.group(2))
            tag = "ol" if is_ol else "ul"

            if not list_stack or indent > list_stack[-1][1]:
                html_out.append(f'<{tag} class="doc-{tag}">')
                list_stack.append((tag, indent))
            elif indent < list_stack[-1][1]:
                close_lists_up_to(indent)
                if not list_stack or list_stack[-1][1] != indent:
                    html_out.append(f'<{tag} class="doc-{tag}">')
                    list_stack.append((tag, indent))
            elif list_stack[-1][0] != tag:
                t_tag, _ = list_stack.pop()
                html_out.append(f"</{t_tag}>")
                html_out.append(f'<{tag} class="doc-{tag}">')
                list_stack.append((tag, indent))

            html_out.append(f'<li class="doc-li">{content}</li>')
            i += 1
            continue

        # Regular paragraph
        close_lists_up_to(-1)
        html_out.append(f'<p class="doc-p">{inline_md(stripped)}</p>')
        i += 1

    close_lists_up_to(-1)
    if in_table:
        flush_table()

    return "\n".join(html_out)


def generate_interactive_html(
    audio_file_path: Path,
    markdown_content: str,
    output_html_path: Path,
    template_path: Path = None,
    auto_open: bool = False
) -> Path:
    """
    Renders standalone interactive meeting player HTML from markdown content
    and an external template.
    """
    if template_path is None:
        template_path = Path(__file__).parent.parent / "assets" / "player_template.html"

    if not template_path.exists():
        raise FileNotFoundError(f"Player HTML template not found: {template_path}")

    template_str = template_path.read_text(encoding="utf-8")
    rel_audio_path = os.path.relpath(audio_file_path, output_html_path.parent)

    # Structurally split Markdown into Minutes Summary vs Verbatim Transcript
    from scripts.canonicalizer import find_transcript_boundary
    summary_part, header_line, transcript_body = find_transcript_boundary(markdown_content)

    # Parse Transcript lines into interactive HTML Turn Cards
    turn_pattern = re.compile(r'^\s*\[(\d+:\d+(?::\d+)?)\s*-\s*(\d+:\d+(?::\d+)?)\]\s*(?:\*\*([^*]+)\*\*[:：])?\s*(.*)')
    turns_html = []
    speaker_class_map = {}
    palette_classes = ["spk-mayor", "spk-emcee", "spk-chief", "spk-default"]

    for line in transcript_body.splitlines():
        line = line.strip()
        if not line:
            continue
        m = turn_pattern.match(line)
        if m:
            start_str, end_str, speaker_str, speech_text = m.groups()
            s_sec = parse_timestamp_to_seconds(start_str)
            e_sec = parse_timestamp_to_seconds(end_str)
            speaker_str = (speaker_str or "").strip()

            badge_html = ""
            if speaker_str:
                spk_lower = speaker_str.lower()
                if any(tok in spk_lower for tok in ["overlap", "simultaneous"]):
                    badge_class = "spk-overlap"
                else:
                    if speaker_str not in speaker_class_map:
                        speaker_class_map[speaker_str] = palette_classes[len(speaker_class_map) % len(palette_classes)]
                    badge_class = speaker_class_map[speaker_str]
                badge_html = f'<span class="speaker-tag {badge_class}">{html.escape(speaker_str)}</span>'

            turn_card = f"""
            <div class="transcript-turn" data-start="{s_sec}" data-end="{e_sec}">
                <div class="turn-header">
                    <button class="ts-badge" onclick="seekAudio({s_sec})" data-i18n-title="seek_badge_tooltip" title="Click to seek playback">
                        <svg class="play-icon" viewBox="0 0 24 24" width="12" height="12"><polygon points="5,3 19,12 5,21" fill="currentColor"/></svg>
                        <span>{start_str} - {end_str}</span>
                    </button>
                    {badge_html}
                </div>
                <div class="turn-text">{html.escape(speech_text)}</div>
            </div>
            """
            turns_html.append(turn_card)
        elif line.startswith("#"):
            turns_html.append(f"<h3 class='transcript-subtitle'>{html.escape(line.lstrip('# '))}</h3>")
        elif line:
            turns_html.append(f"<p class='transcript-plain'>{html.escape(line)}</p>")

    parsed_transcript_html = "\n".join(turns_html)
    summary_html = parse_markdown_to_html(summary_part)

    # Replace placeholders in template
    rendered_html = template_str.replace("{{TITLE}}", html.escape(audio_file_path.stem))
    rendered_html = rendered_html.replace("{{AUDIO_SRC}}", html.escape(rel_audio_path))
    rendered_html = rendered_html.replace("{{SUMMARY_HTML}}", summary_html)
    rendered_html = rendered_html.replace("{{TRANSCRIPT_TURNS}}", parsed_transcript_html)

    output_html_path.write_text(rendered_html, encoding="utf-8")
    print(f"[*] 🌐 Interactive meeting player HTML generated: {output_html_path}")

    if auto_open:
        try:
            import webbrowser
            webbrowser.open(output_html_path.resolve().as_uri())
            print(f"[*] 🚀 Opened interactive player in default browser: {output_html_path.name}")
        except Exception as e:
            print(f"[!] Failed to open browser automatically: {e}")

    return output_html_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate interactive meeting player HTML from markdown & audio.")
    parser.add_argument("audio", help="Path to audio file (mp3, m4a, wav, etc.)")
    parser.add_argument("markdown", help="Path to structured meeting minutes markdown file")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: <audio_stem>_player.html)")
    parser.add_argument("--open", action="store_true", help="Automatically open generated HTML in browser")

    args = parser.parse_args()
    audio_p = Path(args.audio)
    md_p = Path(args.markdown)
    if not audio_p.exists():
        print(f"[!] Audio file not found: {audio_p}")
        exit(1)
    if not md_p.exists():
        print(f"[!] Markdown file not found: {md_p}")
        exit(1)

    out_p = Path(args.output) if args.output else audio_p.parent / f"{audio_p.stem}_player.html"
    md_text = md_p.read_text(encoding="utf-8")
    generate_interactive_html(audio_p, md_text, out_p, auto_open=args.open)
