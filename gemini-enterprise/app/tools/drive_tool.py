"""
app/tools/drive_tool.py - Google Drive Integration & Low-Memory Streaming to GCS.
Enables direct ingestion of Google Meet recordings (.mp4) and audio files stored in Google Drive.
"""

import io
import re
from pathlib import Path
from typing import Any
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.cloud import storage

from .gcs_tool import get_gcs_client


def extract_drive_file_id(drive_url_or_id: str) -> str:
    """
    Extract Google Drive file ID from standard sharing URLs, folder links, or raw IDs.
    Examples:
      - https://drive.google.com/file/d/1A2B3C4D5E/view
      - https://drive.google.com/open?id=1A2B3C4D5E
      - 1A2B3C4D5E
    """
    text = str(drive_url_or_id).strip()
    match = re.search(r'(?:file\/d\/|id=|\/folders\/|open\?id=)([a-zA-Z0-9_-]{25,})', text)
    if match:
        return match.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{25,}$', text):
        return text
    raise ValueError(f"Could not extract a valid Google Drive file ID from: {drive_url_or_id}")


def get_drive_service(credentials=None):
    """Initialize and return Google Drive v3 API service."""
    if credentials is None:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"]
        )
    return build("drive", "v3", credentials=credentials)


def get_drive_file_metadata(file_id: str, drive_service=None) -> dict[str, Any]:
    """Retrieve metadata for a Google Drive file."""
    service = drive_service or get_drive_service()
    metadata = service.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, createdTime"
    ).execute()
    return metadata


def stream_drive_file_to_gcs(
    drive_url_or_id: str,
    bucket_name: str,
    gcs_prefix: str = "raw",
    drive_service=None,
    gcs_client: storage.Client = None,
    chunk_size_mb: int = 10,
) -> tuple[str, str, dict[str, Any]]:
    """
    Stream a file from Google Drive directly into a Google Cloud Storage bucket
    using chunked streaming to prevent high memory consumption on large video files.

    Returns:
      (gcs_uri, file_name, file_metadata)
    """
    file_id = extract_drive_file_id(drive_url_or_id)
    service = drive_service or get_drive_service()
    client = gcs_client or get_gcs_client()

    meta = get_drive_file_metadata(file_id, drive_service=service)
    file_name = meta.get("name", f"drive_{file_id}")
    mime_type = meta.get("mimeType", "application/octet-stream")
    file_size_bytes = int(meta.get("size", 0))

    # Determine destination prefix based on media type
    is_video = "video" in mime_type or Path(file_name).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
    subfolder = "videos" if is_video else "audios"
    destination_blob_name = f"{gcs_prefix}/{subfolder}/{file_name}"

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.content_type = mime_type

    print(f"[*] Streaming Google Drive file '{file_name}' ({file_size_bytes / (1024*1024):.1f} MB) -> gs://{bucket_name}/{destination_blob_name}...")

    request = service.files().get_media(fileId=file_id)
    chunk_size = chunk_size_mb * 1024 * 1024

    with blob.open("wb", chunk_size=chunk_size) as gcs_stream:
        downloader = MediaIoBaseDownload(gcs_stream, request, chunksize=chunk_size)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"    Streaming progress: {int(status.progress() * 100)}%")

    gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
    print(f"[✓] Google Drive file streamed successfully to {gcs_uri}")
    return gcs_uri, file_name, meta


def export_file_to_drive(
    local_path: Path | str,
    parent_folder_id: str = None,
    drive_service=None,
) -> str:
    """
    Export a generated local minutes (.md) or player (.html) back to Google Drive.
    Returns the webViewLink of the newly created Drive file.
    """
    local_p = Path(local_path).resolve()
    if not local_p.is_file():
        raise FileNotFoundError(f"File not found: {local_p}")

    service = drive_service or get_drive_service()

    file_metadata: dict[str, Any] = {"name": local_p.name}
    if parent_folder_id:
        file_metadata["parents"] = [parent_folder_id]

    media = MediaFileUpload(str(local_p), resumable=True)
    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink"
    ).execute()

    view_link = created.get("webViewLink", "")
    print(f"[✓] Exported to Google Drive: {local_p.name} -> {view_link}")
    return view_link
