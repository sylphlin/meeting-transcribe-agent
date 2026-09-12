"""
scripts/gcs_utils.py - Google Cloud Storage Upload/Download & Signed URL Utility.

Vertex AI has no equivalent of the Gemini Developer API's ephemeral Files API
(client.files.upload/.get/.delete) -- media fed to Gemini via Vertex AI must
be referenced by a gs:// URI instead. This module is the shared replacement:
upload a local file to the raw/ prefix of the meeting-transcribe bucket
(provisioned by terraform/, which auto-deletes raw/ objects after a couple of
days -- see raw_retention_days) and reference the resulting URI directly.
"""

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from google.cloud import storage

# Minimal extension -> MIME type map for the media types this project handles.
# GCS does not infer content type from bytes the way the old Files API did,
# so callers pass this explicitly at upload time (stored as the blob's
# content_type) and reuse the same value as the Gemini Part's mime_type.
_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wma": "audio/x-ms-wma",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".m4v": "video/mp4",
}


def guess_mime_type(path: Path | str) -> str:
    """Best-effort MIME type for a local media file, defaulting to a generic audio type."""
    suffix = Path(path).suffix.lower()
    return _MIME_TYPES.get(suffix, "audio/mpeg")


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """
    Parse a Google Cloud Storage URI (gs://bucket-name/path/to/blob).
    Returns (bucket_name, blob_name).
    """
    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs":
        raise ValueError(f"Invalid GCS URI (must start with gs://): {gcs_uri}")
    bucket_name = parsed.netloc
    blob_name = parsed.path.lstrip("/")
    return bucket_name, blob_name


def get_gcs_client(project_id: str = None) -> storage.Client:
    """Return an authenticated Google Cloud Storage client (via Application Default Credentials)."""
    return storage.Client(project=project_id)


def upload_file_to_gcs(
    local_path: Path | str,
    bucket_name: str,
    destination_blob_name: str,
    content_type: str = None,
    client: storage.Client = None,
) -> str:
    """
    Upload a local file to a GCS bucket.
    Returns the gs:// URI of the uploaded blob.
    """
    local_p = Path(local_path).resolve()
    if not local_p.is_file():
        raise FileNotFoundError(f"Local file not found: {local_p}")

    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    if content_type:
        blob.content_type = content_type

    print(f"[*] Uploading {local_p.name} to gs://{bucket_name}/{destination_blob_name}...")
    blob.upload_from_filename(str(local_p))
    gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
    print(f"[✓] Upload complete: {gcs_uri}")
    return gcs_uri


def delete_gcs_blob(gcs_uri: str, client: storage.Client = None) -> None:
    """
    Delete a blob by its gs:// URI. Used to clean up ephemeral raw/ uploads
    right after Gemini has processed them (the bucket's lifecycle rule is a
    backstop for cases where this cleanup itself doesn't run, not a substitute).
    Missing blobs are treated as already-cleaned-up, not an error.
    """
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    try:
        blob.delete()
    except Exception:
        pass


def download_file_from_gcs(
    gcs_uri: str,
    local_destination_dir: Path | str = None,
    client: storage.Client = None,
) -> Path:
    """
    Download a file from GCS to a local directory.
    Returns the resolved local Path.
    """
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        raise FileNotFoundError(f"Blob gs://{bucket_name}/{blob_name} does not exist.")

    if local_destination_dir is None:
        local_dir = Path("/tmp/meeting_transcribe_downloads")
    else:
        local_dir = Path(local_destination_dir)

    local_dir.mkdir(parents=True, exist_ok=True)
    file_name = Path(blob_name).name
    local_path = local_dir / file_name

    print(f"[*] Downloading gs://{bucket_name}/{blob_name} -> {local_path}...")
    blob.download_to_filename(str(local_path))
    print(f"[✓] Download complete: {local_path} ({local_path.stat().st_size / (1024*1024):.1f} MB)")
    return local_path


def generate_signed_download_url(
    bucket_name: str,
    blob_name: str,
    expiration_hours: int = 24,
    client: storage.Client = None,
) -> str:
    """
    Generate a v4 signed URL for secure browser access and streaming.
    Defaults to 24 hours (86,400 seconds) expiration.
    """
    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(hours=expiration_hours),
        method="GET",
    )
    return url
