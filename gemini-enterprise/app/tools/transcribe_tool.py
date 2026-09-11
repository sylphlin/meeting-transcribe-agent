"""
app/tools/transcribe_tool.py - Unified Enterprise Meeting Transcribe Tool for ADK 2.0.
Supports YouTube URLs, Google Drive video/audio files, and GCS URIs.
Outputs structured executive minutes and interactive HTML player with 24-hour signed URLs.
"""

import os
import json
from pathlib import Path
from typing import Any
import google.auth

from ..core.audio_utils import is_youtube_url, is_video_file
from ..core.html_generator import extract_meeting_title
from ..core.meeting_transcribe import transcribe_meeting
from .gcs_tool import (
    parse_gcs_uri,
    upload_file_to_gcs,
    download_file_from_gcs,
    generate_signed_download_url,
    get_gcs_client,
)
from .drive_tool import (
    extract_drive_file_id,
    stream_drive_file_to_gcs,
    export_file_to_drive,
)


def get_default_bucket_name() -> str:
    """Resolve the default GCS storage bucket from env or project convention."""
    env_bucket = os.getenv("MEETING_STORAGE_BUCKET")
    if env_bucket:
        return env_bucket.replace("gs://", "").strip()

    _, project_id = google.auth.default()
    if not project_id:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "default-project")
    return f"{project_id}-meeting-data"


def process_meeting_transcription(
    source: str,
    bucket_name: str = None,
    summary_language: str = None,
    agentic: bool = False,
    extract_audio: bool = False,
    export_to_drive: bool = False,
    drive_folder_id: str = None,
) -> dict[str, Any]:
    """
    Transcribe and analyze a meeting recording from YouTube, Google Drive, or GCS.

    Args:
        source: Media URL or path. Can be:
            - YouTube URL (e.g. https://www.youtube.com/watch?v=VIDEO_ID)
            - Google Drive URL or ID (e.g. https://drive.google.com/file/d/FILE_ID)
            - Google Cloud Storage URI (e.g. gs://bucket/raw/meeting.mp4)
        bucket_name: Optional GCS bucket name for storing minutes and player HTML.
        summary_language: Target language for executive summary and action items (e.g. 'English', 'Traditional Chinese', 'Japanese').
        agentic: Whether to enable Agentic Video Understanding for complex video slides.
        extract_audio: Whether to force audio extraction from videos for token economy.
        export_to_drive: Whether to upload generated minutes back to Google Drive.
        drive_folder_id: Target Google Drive folder ID for export.

    Returns:
        Structured dictionary containing transcription status, meeting metadata,
        executive summary, GCS URIs, and 24-hour browser signed URLs.
    """
    target_bucket = bucket_name or get_default_bucket_name()
    source_clean = str(source).strip()
    is_yt = is_youtube_url(source_clean)
    is_gcs = source_clean.startswith("gs://")
    is_drive = "drive.google.com" in source_clean or (not is_yt and not is_gcs and len(source_clean) >= 25 and "/" not in source_clean)

    work_dir = Path("/tmp/meeting_transcribe_runtime")
    work_dir.mkdir(parents=True, exist_ok=True)

    local_media_source: str | Path = source_clean
    origin_type = "youtube" if is_yt else ("gcs" if is_gcs else ("drive" if is_drive else "local"))

    print(f"[*] Enterprise Transcribe Tool invoked: {source_clean} (Detected source: {origin_type})")

    # 1. Ingestion Phase
    if is_yt:
        # YouTube URLs stream directly via Gemini Cloud backbone without local download
        local_media_source = source_clean
    elif is_drive:
        # Stream chunked from Google Drive to GCS staging
        gcs_staged_uri, file_name, _ = stream_drive_file_to_gcs(
            drive_url_or_id=source_clean,
            bucket_name=target_bucket,
            gcs_prefix="raw"
        )
        # Download staged media to local container for processing
        local_media_source = download_file_from_gcs(gcs_staged_uri, local_destination_dir=work_dir)
    elif is_gcs:
        # Download from GCS to local container
        local_media_source = download_file_from_gcs(source_clean, local_destination_dir=work_dir)
    else:
        local_media_source = Path(source_clean).resolve()

    # 2. Execution Phase
    output_stem = Path(local_media_source).stem if not is_yt else f"yt_{source_clean[-11:]}"
    dest_minutes_md = work_dir / f"{output_stem}_minutes.md"

    generated_md_path = transcribe_meeting(
        input_source=local_media_source,
        output_file=dest_minutes_md,
        summary_language=summary_language,
        agentic=agentic,
        extract_audio=extract_audio,
        no_player=False,
    )

    generated_html_path = generated_md_path.parent / f"{generated_md_path.stem[:-8]}_player.html"
    markdown_text = generated_md_path.read_text(encoding="utf-8")
    meeting_title = extract_meeting_title(markdown_text) or output_stem

    # 3. GCS Storage Phase
    minutes_blob_name = f"minutes/{generated_md_path.name}"
    player_blob_name = f"players/{generated_html_path.name}"

    gcs_minutes_uri = upload_file_to_gcs(
        local_path=generated_md_path,
        bucket_name=target_bucket,
        destination_blob_name=minutes_blob_name,
        content_type="text/markdown; charset=utf-8"
    )

    gcs_player_uri = upload_file_to_gcs(
        local_path=generated_html_path,
        bucket_name=target_bucket,
        destination_blob_name=player_blob_name,
        content_type="text/html; charset=utf-8"
    )

    # 4. 24-Hour Signed URL Generation
    player_signed_url = generate_signed_download_url(
        bucket_name=target_bucket,
        blob_name=player_blob_name,
        expiration_hours=24
    )

    minutes_signed_url = generate_signed_download_url(
        bucket_name=target_bucket,
        blob_name=minutes_blob_name,
        expiration_hours=24
    )

    # 5. Optional Drive Export
    drive_minutes_link = None
    if export_to_drive:
        try:
            drive_minutes_link = export_file_to_drive(
                local_path=generated_md_path,
                parent_folder_id=drive_folder_id
            )
        except Exception as e:
            print(f"[!] Warning: Could not export to Google Drive: {e}")

    # Extract quick overview from Markdown Section 2 if available
    summary_snippet = ""
    summary_match = markdown_text.split("## 2. ")
    if len(summary_match) > 1:
        next_sec = summary_match[1].split("## 3. ")[0]
        summary_snippet = next_sec.strip()[:600]

    return {
        "status": "success",
        "meeting_title": meeting_title,
        "source": source_clean,
        "origin_type": origin_type,
        "target_bucket": target_bucket,
        "gcs_minutes_uri": gcs_minutes_uri,
        "gcs_player_uri": gcs_player_uri,
        "player_signed_url": player_signed_url,
        "minutes_signed_url": minutes_signed_url,
        "drive_minutes_link": drive_minutes_link,
        "executive_summary_preview": summary_snippet,
    }
