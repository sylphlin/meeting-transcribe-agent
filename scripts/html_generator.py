"""
scripts/html_generator.py - Standalone Interactive Meeting Player Generator.
Lightweight Data-Driven Template Hydrator.
Embeds audio path and structured markdown into the standalone HTML player template.
Client-side rendering via marked.js ensures 100% GFM markdown compliance with zero Python overhead.
"""

import os
import json
import html
from pathlib import Path


def generate_interactive_html(
    audio_file_path: Path,
    markdown_content: str,
    output_html_path: Path,
    template_path: Path = None,
) -> Path:
    """
    Renders standalone interactive meeting player HTML from markdown content
    and an external template by injecting a structured JSON payload.
    """
    if template_path is None:
        template_path = Path(__file__).parent.parent / "assets" / "player_template.html"

    if not template_path.exists():
        raise FileNotFoundError(f"Player HTML template not found: {template_path}")

    template_str = template_path.read_text(encoding="utf-8")
    rel_audio_path = os.path.relpath(audio_file_path, output_html_path.parent)

    title_safe = html.escape(audio_file_path.stem)
    payload = {
        "title": audio_file_path.stem,
        "audioSrc": rel_audio_path,
        "markdown": markdown_content
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    rendered_html = template_str.replace("{{TITLE}}", title_safe)
    rendered_html = rendered_html.replace("{{AUDIO_SRC}}", html.escape(rel_audio_path))
    rendered_html = rendered_html.replace("/* __MEETING_DATA__ */", f"window.MEETING_DATA = {payload_json};")

    output_html_path.write_text(rendered_html, encoding="utf-8")
    print(f"[*] 🌐 Standalone interactive player HTML generated: {output_html_path}")

    return output_html_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate interactive meeting player HTML from markdown & audio.")
    parser.add_argument("audio", help="Path to audio file (mp3, m4a, wav, etc.)")
    parser.add_argument("markdown", help="Path to structured meeting minutes markdown file")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: <audio_stem>_player.html)")

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
    generate_interactive_html(audio_p, md_text, out_p)
