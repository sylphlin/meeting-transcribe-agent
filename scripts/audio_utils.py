"""
scripts/audio_utils.py - Audio processing utilities for Meeting Transcribe Agent.
Handles duration extraction, timestamp formatting, and ffmpeg pre-compression.
Guarantees ASCII-safe file basenames to prevent google-genai httpx header UnicodeEncodeError.
"""

import os
import shutil
import hashlib
import subprocess
from pathlib import Path
from contextlib import contextmanager


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def format_offset(seconds: float) -> str:
    """Format seconds into MM:SS."""
    if seconds is None:
        return "00:00"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def compress_audio_for_upload(audio_path: Path, bitrate: str = "64k") -> Path:
    """
    Compress audio to low-bitrate AAC with an ASCII-safe filename.
    Essential because google-genai sets HTTP header 'X-Goog-Upload-File-Name'
    from the basename, which httpx encodes as ASCII.
    """
    safe_hash = hashlib.md5(audio_path.name.encode("utf-8")).hexdigest()[:12]
    out_path = audio_path.parent / f"upload_temp_{safe_hash}_{bitrate}.m4a"
    print(f"[*] 壓縮音檔供快速上傳 ({bitrate})...")
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", bitrate,
        str(out_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    orig_mb = audio_path.stat().st_size / (1024 * 1024)
    new_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[*] 壓縮完成：{orig_mb:.1f} MB -> {new_mb:.1f} MB (節省 {(1 - new_mb/orig_mb)*100:.1f}%)")
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
