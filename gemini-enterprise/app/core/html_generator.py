"""
scripts/html_generator.py - Standalone Interactive Meeting Player Generator.
Lightweight Data-Driven Template Hydrator.
Embeds audio path and structured markdown into the standalone HTML player template.
Client-side rendering via marked.js ensures 100% GFM markdown compliance with zero Python overhead.
"""

import os
import re
import json
import html
from pathlib import Path


def extract_meeting_title(markdown_content: str) -> str | None:
    """Extract official meeting title from Markdown Section 1 metadata."""
    if not markdown_content:
        return None
    patterns = [
        r'[-*]\s*\*\*(?:會議名稱|會議主題|會議名|会議名|議題|Meeting Title|Title|Titre|Titel|Tema de la reunión)\*\*\s*[:：]\s*([^\n\r]+)',
        r'#+\s*(?:1\.\s*)?(?:會議名稱|Meeting Title|Title)\s*[:：]\s*([^\n\r]+)',
        r'[-*]\s*\*\*([^*]*(?:Title|名稱|名|主題|Titre|Titel)[^*]*)\*\*\s*[:：]\s*([^\n\r]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, markdown_content, re.IGNORECASE)
        if m:
            clean = m.group(m.lastindex).strip()
            clean = clean.strip('*_`# ')
            if clean:
                return clean
    return None


def generate_interactive_html(
    media_source: Path | str = None,
    markdown_content: str = "",
    output_html_path: Path = None,
    template_path: Path = None,
    audio_file_path: Path | str = None,
    title: str = None,
) -> Path:
    """
    Renders standalone interactive meeting player HTML from markdown content
    and an external template by injecting a structured JSON payload.
    Automatically chooses between dedicated audio and video templates based on media type.
    Supports local audio files, local video files, and YouTube URLs.
    """
    from .audio_utils import is_youtube_url, extract_youtube_id, is_video_file, fetch_youtube_title

    source = media_source if media_source is not None else audio_file_path
    if source is None:
        raise ValueError("Either media_source or audio_file_path must be provided.")

    source_str = str(source).strip()
    assets_dir = Path(__file__).parent.parent / "assets"

    # Determine media type and default template
    if is_youtube_url(source_str):
        media_type = "youtube"
        default_template = assets_dir / "video_player_template.html"
    elif is_video_file(source_str):
        media_type = "local_video"
        default_template = assets_dir / "video_player_template.html"
    else:
        media_type = "audio"
        default_template = assets_dir / "audio_player_template.html"

    selected_template = template_path or default_template
    if not selected_template.exists():
        raise FileNotFoundError(f"Player HTML template not found: {selected_template}")

    template_str = selected_template.read_text(encoding="utf-8")
    rel_media_path = ""
    md_meeting_title = extract_meeting_title(markdown_content)

    if media_type == "youtube":
        yt_id = extract_youtube_id(source_str) or "youtube"
        # Priority: 1) explicit title arg, 2) extracted from markdown Section 1, 3) oEmbed YouTube title, 4) fallback
        yt_oembed_title = fetch_youtube_title(source_str) if not md_meeting_title else None
        title_name = title or md_meeting_title or yt_oembed_title or f"YouTube Meeting ({yt_id})"
        payload = {
            "title": title_name,
            "mediaType": "youtube",
            "youtubeId": yt_id,
            "youtubeUrl": source_str,
            "videoSrc": "",
            "audioSrc": "",
            "markdown": markdown_content
        }
        title_safe = html.escape(title_name)
    elif media_type == "local_video":
        media_path = Path(source_str).resolve()
        rel_media_path = os.path.relpath(media_path, output_html_path.parent)
        title_name = title or md_meeting_title or media_path.stem
        title_safe = html.escape(title_name)
        payload = {
            "title": title_name,
            "mediaType": "local_video",
            "youtubeId": None,
            "youtubeUrl": None,
            "videoSrc": rel_media_path,
            "audioSrc": rel_media_path,
            "markdown": markdown_content
        }
    else:
        media_path = Path(source_str).resolve()
        rel_media_path = os.path.relpath(media_path, output_html_path.parent)
        title_name = title or md_meeting_title or media_path.stem
        title_safe = html.escape(title_name)
        payload = {
            "title": title_name,
            "mediaType": "audio",
            "youtubeId": None,
            "youtubeUrl": None,
            "videoSrc": None,
            "audioSrc": rel_media_path,
            "markdown": markdown_content
        }

    payload_json = json.dumps(payload, ensure_ascii=False)

    rendered_html = template_str.replace("{{TITLE}}", title_safe)
    rendered_html = rendered_html.replace("{{AUDIO_SRC}}", html.escape(rel_media_path))
    rendered_html = rendered_html.replace("{{VIDEO_SRC}}", html.escape(rel_media_path))
    rendered_html = rendered_html.replace("/* __MEETING_DATA__ */", f"window.MEETING_DATA = {payload_json};")

    output_html_path.write_text(rendered_html, encoding="utf-8")
    print(f"[*] 🌐 Standalone interactive player HTML generated ({media_type}): {output_html_path}")

    if media_type == "youtube":
        print(f"[*] 💡 [YouTube Notice] Due to YouTube security policies, opening via file:// triggers Error 153.")
        print(f"[*]    To enable full embedded video sync, serve via a local HTTP server:")
        print(f"[*]    👉 python3 -m http.server 8000 --directory \"{output_html_path.parent}\"")
        print(f"[*]    then open: http://localhost:8000/{output_html_path.name}")

    return output_html_path



def serve_html_player(html_path: Path, port: int = 8000):
    """
    Spins up a lightweight local HTTP server and opens the browser.
    Ensures embedded YouTube videos and local assets comply with web origin policies.
    """
    import http.server
    import socketserver
    import webbrowser
    import socket

    html_path = html_path.resolve()
    serve_dir = str(html_path.parent)

    # Find available port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                break
            port += 1

    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=serve_dir, **kwargs)

        def log_message(self, format, *args):
            pass  # Quiet logging

    target_url = f"http://localhost:{port}/{html_path.name}"
    print(f"\n========================================================")
    print(f"🚀 Serving Meeting Transcribe Player at:")
    print(f"   {target_url}")
    print(f"   (Press Ctrl+C to stop local server)")
    print(f"========================================================\n")

    webbrowser.open(target_url)

    with socketserver.TCPServer(("localhost", port), CustomHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[*] Local HTTP server stopped.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate interactive meeting player HTML from markdown & audio/video.")
    parser.add_argument("media", help="Path to audio/video file or YouTube URL")
    parser.add_argument("markdown", help="Path to structured meeting minutes markdown file")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: <stem>_player.html)")
    parser.add_argument("--serve", action="store_true", help="Automatically launch local HTTP server and open browser")

    args = parser.parse_args()
    source_str = args.media.strip()
    md_p = Path(args.markdown)

    if not md_p.exists():
        print(f"[!] Markdown file not found: {md_p}")
        exit(1)

    try:
        from .audio_utils import is_youtube_url, extract_youtube_id
    except (ImportError, ModuleNotFoundError):
        from audio_utils import is_youtube_url, extract_youtube_id
    if not is_youtube_url(source_str):
        media_p = Path(source_str)
        if not media_p.exists():
            print(f"[!] Media file not found: {media_p}")
            exit(1)
        default_out = media_p.parent / f"{media_p.stem}_player.html"
    else:
        yt_id = extract_youtube_id(source_str) or "youtube"
        default_out = Path.cwd() / f"{yt_id}_player.html"

    out_p = Path(args.output) if args.output else default_out
    md_text = md_p.read_text(encoding="utf-8")
    generated = generate_interactive_html(source_str, md_text, out_p)

    if args.serve:
        serve_html_player(generated)

