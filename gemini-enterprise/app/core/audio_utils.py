"""
scripts/audio_utils.py - Audio processing utilities for Meeting Transcribe Agent.
Handles duration extraction, timestamp formatting, and ffmpeg pre-compression.
Guarantees ASCII-safe file basenames to prevent google-genai httpx header UnicodeEncodeError.
"""

import shutil
import hashlib
import subprocess
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from contextlib import contextmanager

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv', '.m4v'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma'}


def is_youtube_url(url_str: str) -> bool:
    """Check if the input string is a valid YouTube URL."""
    if not isinstance(url_str, str):
        return False
    url_str = url_str.strip()
    return bool(re.search(r'(?:youtube\.com\/(?:watch|live|shorts|embed)|youtu\.be\/)', url_str, re.IGNORECASE))


def extract_youtube_id(url_str: str) -> str | None:
    """Extract 11-character YouTube video ID from various YouTube URL formats."""
    if not is_youtube_url(url_str):
        return None
    url_str = url_str.strip()
    match = re.search(r'(?:v=|\/embed\/|\/shorts\/|\/live\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', url_str)
    if match:
        return match.group(1)
    try:
        parsed = urlparse(url_str)
        qs = parse_qs(parsed.query)
        if 'v' in qs and qs['v']:
            return qs['v'][0]
    except Exception:
        pass
    return None


def fetch_youtube_title(url_str: str) -> str | None:
    """
    Fetch the YouTube video title via YouTube's official oEmbed API (zero credentials required).
    Returns title string or None on failure/offline.
    """
    if not is_youtube_url(url_str):
        return None
    import urllib.request
    import urllib.parse
    import json
    import ssl
    try:
        ctx = ssl._create_unverified_context()
        encoded_url = urllib.parse.quote(url_str.strip())
        oembed_url = f"https://www.youtube.com/oembed?url={encoded_url}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            title = data.get("title")
            if title:
                return title.strip()
    except Exception:
        pass
    return None


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """
    Sanitize a string to be safely used as a filename across macOS, Windows, and Linux.
    Replaces illegal filename characters with clean separators and trims length.
    """
    if not name:
        return ""
    # Strip markdown formatting
    cleaned = re.sub(r'[*_`#~]', '', name)
    # Replace illegal filename characters: \ / : * ? " < > |
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', cleaned)
    # Collapse multiple spaces/underscores
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'_+', '_', cleaned)
    # Strip leading/trailing dots or spaces
    cleaned = cleaned.strip('. _')
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    return cleaned


def is_video_file(path: Path | str) -> bool:
    """Check if the given path is a recognized video file."""
    try:
        p = Path(path)
        return p.suffix.lower() in VIDEO_EXTENSIONS
    except Exception:
        return False


def extract_audio_from_video(video_path: Path, output_path: Path = None, bitrate: str = "64k") -> Path:
    """
    Extract a high-efficiency 16kHz mono audio track from a video file via ffmpeg.
    Useful for local playback and fallback audio pipelines.
    """
    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    if output_path is None:
        output_path = video_path.parent / f"{video_path.stem}.m4a"
    else:
        output_path = Path(output_path).resolve()

    if output_path.exists() and output_path.stat().st_size > 0:
        cached_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[✓] Found existing extracted audio: {output_path.name} ({cached_mb:.1f} MB). Reusing.")
        return output_path

    print(f"[*] Extracting audio from video: {video_path.name} -> {output_path.name}...")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "aac", "-b:a", bitrate,
        str(output_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[✓] Extracted audio: {output_path.name} ({size_mb:.1f} MB)")
    return output_path


def optimize_video_for_upload(video_path: Path, output_path: Path = None, max_size_mb: float = 250.0) -> Path:
    """
    If a video file is larger than max_size_mb, downscale/compress it to 720p H.264
    for fast upload to Gemini Files API while preserving crisp presentation text and nameplates.
    """
    video_path = Path(video_path).resolve()
    orig_mb = video_path.stat().st_size / (1024 * 1024)
    if orig_mb <= max_size_mb:
        return video_path

    if output_path is None:
        safe_hash = hashlib.md5(video_path.name.encode("utf-8")).hexdigest()[:8]
        output_path = video_path.parent / f"optimized_{safe_hash}.mp4"
    else:
        output_path = Path(output_path).resolve()

    if output_path.exists() and output_path.stat().st_size > 0:
        cached_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"[✓] Found existing optimized video: {output_path.name} ({cached_mb:.1f} MB). Reusing.")
        return output_path

    print(f"[*] Video size is {orig_mb:.1f} MB (> {max_size_mb} MB). Optimizing to 720p for fast cloud upload...")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", "scale=-2:720",
        "-c:v", "libx264", "-crf", "28", "-preset", "faster",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "16000",
        str(output_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    new_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[✓] Optimized video: {orig_mb:.1f} MB -> {new_mb:.1f} MB (saved {(1 - new_mb/orig_mb)*100:.1f}%)")
    return output_path



def get_audio_duration(audio_path: Path) -> float:
    """
    Get audio duration in seconds via multi-layer probing:
    1. Container format duration (format=duration)
    2. Audio stream duration (stream=duration)
    3. Fallback calculation via file size and detected bitrate
    """
    audio_path = Path(audio_path)
    # Layer 1: format=duration
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = res.stdout.strip()
        if val and val.lower() != "n/a":
            d = float(val)
            if d > 0:
                return d
    except Exception:
        pass

    # Layer 2: stream=duration
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = res.stdout.strip()
        if val and val.lower() != "n/a":
            d = float(val)
            if d > 0:
                return d
    except Exception:
        pass

    # Layer 3: Calculate from bitrate if available
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = res.stdout.strip()
        if val.isdigit() and int(val) > 0:
            bitrate = int(val)
            size_bytes = audio_path.stat().st_size
            return float((size_bytes * 8) / bitrate)
    except Exception:
        pass

    return 0.0


def detect_silence_in_window(
    audio_path: Path,
    win_start: float,
    win_dur: float,
    noise_db: str = "-30dB",
    min_silence_sec: float = 0.3
) -> list[tuple[float, float]]:
    """
    Detect silence intervals in a specific time window using ffmpeg silencedetect filter.
    Returns list of (start_sec, end_sec) relative to win_start.
    """
    cmd = [
        "ffmpeg", "-v", "info",
        "-ss", str(win_start),
        "-t", str(win_dur),
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}:d={min_silence_sec}",
        "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, check=False)
        intervals = []
        cur_start = None
        for line in res.stderr.splitlines():
            if "silence_start:" in line:
                m = re.search(r'silence_start:\s*([0-9.]+)', line)
                if m:
                    cur_start = float(m.group(1))
            elif "silence_end:" in line:
                m = re.search(r'silence_end:\s*([0-9.]+)', line)
                if m and cur_start is not None:
                    intervals.append((cur_start, float(m.group(1))))
                    cur_start = None
        if cur_start is not None:
            intervals.append((cur_start, win_dur))
        return intervals
    except Exception:
        return []


def find_silence_cut_points(
    audio_path: Path,
    total_duration: float,
    max_chunk_sec: float = 840.0,
    search_window_sec: float = 60.0
) -> list[tuple[float, float]]:
    """
    Greedy forward split:
    Target chunk duration is max_chunk_sec (default 14 minutes = 840s).
    From current_start, if remaining <= max_chunk_sec, process as final chunk.
    Otherwise, search in [current_start + max_chunk_sec - search_window_sec, current_start + max_chunk_sec]
    (e.g. 13m ~ 14m) for the best silence interval using FFmpeg silencedetect.
    Splits at the midpoint of that silence interval.
    If no silence is found, safely falls back to current_start + max_chunk_sec - (search_window_sec / 2.0).
    Returns list of (chunk_start, chunk_end) in seconds.
    """
    if total_duration <= max_chunk_sec:
        return [(0.0, total_duration)]

    chunks = []
    current_start = 0.0

    while current_start < total_duration:
        remaining = total_duration - current_start
        if remaining <= max_chunk_sec:
            chunks.append((current_start, total_duration))
            break

        win_start = current_start + max_chunk_sec - search_window_sec
        win_dur = min(search_window_sec, total_duration - win_start)

        # 1. Search for silence at -30dB (standard speech pause)
        intervals = detect_silence_in_window(audio_path, win_start, win_dur, noise_db="-30dB", min_silence_sec=0.3)
        # 2. Relax to -25dB if no silence detected
        if not intervals:
            intervals = detect_silence_in_window(audio_path, win_start, win_dur, noise_db="-25dB", min_silence_sec=0.2)

        if intervals:
            # Pick the silence interval closest to the end of the window to maximize segment capacity
            # while guaranteeing the chunk stays <= max_chunk_sec
            best_interval = intervals[-1]
            split_pt = win_start + (best_interval[0] + best_interval[1]) / 2.0
        else:
            # Safe deterministic fallback: middle of the search window (e.g. 13.5 min)
            split_pt = win_start + (win_dur / 2.0)

        # Clamp split_pt to stay strictly within (current_start, total_duration) and <= current_start + max_chunk_sec
        split_pt = max(current_start + 60.0, min(split_pt, current_start + max_chunk_sec, total_duration))

        chunks.append((current_start, split_pt))
        current_start = split_pt

    return chunks


def get_audio_bitrate(audio_path: Path) -> int:
    """Get audio bitrate in bps via ffprobe, or calculate from size/duration."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        val = res.stdout.strip()
        if val.isdigit() and int(val) > 0:
            return int(val)
    except Exception:
        pass

    try:
        dur = get_audio_duration(audio_path)
        if dur > 0:
            size_bytes = audio_path.stat().st_size
            return int((size_bytes * 8) / dur)
    except Exception:
        pass
    return 0


def should_compress_audio(
    audio_path: Path,
    max_size_mb: float = 10.0,
    target_bitrate_kbps: int = 48
) -> tuple[bool, str]:
    """
    Decide whether audio file requires FFmpeg re-encoding.
    Returns (should_compress, reason).
    Skips compression if:
      - File size is already <= max_size_mb
      - Existing bitrate is already <= target_bitrate_kbps
    """
    size_mb = audio_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_size_mb:
        return False, f"file size is {size_mb:.1f} MB (<= {max_size_mb} MB threshold)"

    current_bps = get_audio_bitrate(audio_path)
    target_bps = target_bitrate_kbps * 1000
    if current_bps > 0 and current_bps <= target_bps:
        current_kbps = current_bps // 1000
        return False, f"original bitrate is {current_kbps} kbps (<= {target_bitrate_kbps} kbps target)"

    return True, f"file size is {size_mb:.1f} MB (> {max_size_mb} MB) and bitrate is {current_bps // 1000 if current_bps else 'unknown'} kbps"


def format_offset(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS for long meetings."""
    if seconds is None:
        return "00:00"
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def compress_audio_for_upload(audio_path: Path, bitrate: str = "64k") -> Path:
    """
    Compress audio to low-bitrate AAC with an ASCII-safe filename.
    Essential because google-genai sets HTTP header 'X-Goog-Upload-File-Name'
    from the basename, which httpx encodes as ASCII.
    """
    safe_hash = hashlib.md5(audio_path.name.encode("utf-8")).hexdigest()[:12]
    out_path = audio_path.parent / f"upload_temp_{safe_hash}_{bitrate}.m4a"
    print(f"[*] Compressing audio for fast upload ({bitrate})...")
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", bitrate,
        str(out_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    orig_mb = audio_path.stat().st_size / (1024 * 1024)
    new_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[*] Compression complete: {orig_mb:.1f} MB -> {new_mb:.1f} MB (saved {(1 - new_mb/orig_mb)*100:.1f}%)")
    return out_path


@contextmanager
def safe_ascii_upload_path(file_path: Path):
    """
    Context manager ensuring the file path presented to Google Files API has
    an ASCII-only basename. Creates a temporary symlink (or copy) if needed.
    """
    file_path = Path(file_path).resolve()
    try:
        file_path.name.encode('ascii')
        yield file_path
    except UnicodeEncodeError:
        safe_hash = hashlib.md5(file_path.name.encode('utf-8')).hexdigest()[:12]
        temp_link = file_path.parent / f"upload_raw_{safe_hash}{file_path.suffix}"
        created = False
        try:
            if temp_link.exists() or temp_link.is_symlink():
                temp_link.unlink()
            temp_link.symlink_to(file_path)
            created = True
        except Exception:
            shutil.copy2(file_path, temp_link)
            created = True
        try:
            yield temp_link
        finally:
            if created and (temp_link.exists() or temp_link.is_symlink()):
                try:
                    temp_link.unlink()
                except Exception:
                    pass


def detect_embedded_subtitles(video_path: Path | str) -> str | None:
    """
    Detect whether the video contains any embedded subtitle stream (e.g. mov_text, subrip, srt, webvtt).
    Returns codec name if detected (e.g. 'mov_text', 'subrip'), otherwise None.
    """
    video_p = Path(video_path).resolve()
    if not video_p.is_file():
        return None
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=codec_name",
            "-of", "csv=p=0",
            str(video_p)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        codecs = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
        if codecs:
            return codecs[0]
    except Exception:
        pass
    return None


def extract_embedded_subtitles(video_path: Path | str, output_path: Path | str = None) -> Path | None:
    """
    Extract embedded subtitle stream from video to an SRT file using ffmpeg.
    Returns the Path to the extracted .srt file if successful, or None if no subtitle stream or error.
    """
    video_p = Path(video_path).resolve()
    if not video_p.is_file():
        return None

    codec = detect_embedded_subtitles(video_p)
    if not codec:
        return None

    if output_path is None:
        safe_hash = hashlib.md5(video_p.name.encode("utf-8")).hexdigest()[:8]
        out_p = video_p.parent / f"{video_p.stem}_{safe_hash}_subtitles.srt"
    else:
        out_p = Path(output_path).resolve()

    if out_p.exists() and out_p.stat().st_size > 0:
        print(f"[✓] Reusing existing extracted subtitles: {out_p.name}")
        return out_p

    out_p.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] Extracting embedded subtitle stream ({codec}): {video_p.name} -> {out_p.name}...")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_p),
            "-map", "0:s:0",
            "-c:s", "srt",
            str(out_p)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and out_p.exists() and out_p.stat().st_size > 0:
            print(f"[✓] Subtitle extraction successful: {out_p.name} ({out_p.stat().st_size} bytes)")
            return out_p
        else:
            if out_p.exists():
                out_p.unlink()
    except Exception as e:
        print(f"[!] Warning: Subtitle extraction failed ({e}).")
        if out_p.exists():
            try:
                out_p.unlink()
            except Exception:
                pass
    return None

