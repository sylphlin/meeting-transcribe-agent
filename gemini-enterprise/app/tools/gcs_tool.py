"""
app/tools/gcs_tool.py - Google Cloud Storage Management & Signed URL Utility.
Handles GCS object upload, download, URI parsing, and 24-hour signed URLs for browser streaming.
"""

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from google.cloud import storage


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
    """Return an authenticated Google Cloud Storage client."""
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
        local_dir = Path("/tmp/gemini_enterprise_downloads")
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
