"""
scripts/gcs_utils.py - Google Cloud Storage Upload/Download & Signed URL Utility.

Vertex AI has no equivalent of the Gemini Developer API's ephemeral Files API
(client.files.upload/.get/.delete) -- media fed to Gemini via Vertex AI must
be referenced by a gs:// URI instead. This module is the shared replacement:
upload a local file to the raw/ prefix of the meeting-transcribe bucket
(configured with lifecycle rules that auto-delete raw/ objects after a couple
of days) and reference the resulting URI directly.
"""

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from google.cloud import storage

import hashlib
import os
import re

GDRIVE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/drive.readonly",
]


def compute_file_sha256(filepath: Path | str) -> str:
    """Compute SHA-256 hex digest of a local file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_file_md5(filepath: Path | str) -> str:
    """Compute MD5 hex digest of a local file (matches Google Drive md5Checksum)."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_gdrive_source(val: str | Path | None) -> bool:
    """Return True if val is a Google Drive URL or gdrive:// URI."""
    if not val:
        return False
    s = str(val).strip()
    return (
        "drive.google.com" in s
        or "docs.google.com" in s
        or s.startswith("gdrive://")
    )


def parse_gdrive_url(url_or_id: str) -> dict:
    """
    Parse a Google Drive file/folder URL or ID into {'id': <id>, 'type': 'file'|'folder'|'unknown'}.
    """
    s = str(url_or_id).strip()
    if s.startswith("gdrive://"):
        rest = s[len("gdrive://"):].strip("/")
        if rest.startswith("folder/"):
            return {"id": rest.split("/", 1)[1].split("?")[0], "type": "folder"}
        if rest.startswith("file/"):
            return {"id": rest.split("/", 1)[1].split("?")[0], "type": "file"}
        return {"id": rest.split("?")[0], "type": "unknown"}

    m_folder = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", s)
    if m_folder:
        return {"id": m_folder.group(1), "type": "folder"}

    m_file = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", s)
    if m_file:
        return {"id": m_file.group(1), "type": "file"}

    m_id = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", s)
    if m_id:
        return {"id": m_id.group(1), "type": "unknown"}

    if re.match(r"^[a-zA-Z0-9_-]{15,}$", s) and not Path(s).exists():
        return {"id": s, "type": "unknown"}

    raise ValueError(f"Invalid Google Drive URL or ID: {url_or_id}")


def get_gdrive_session(project_id: str = None):
    """
    Return an HTTP session using Service Account Impersonation (via standard cloud-platform ADC)
    or public requests.Session() fallback so personal gcloud logins NEVER hit 'This app is blocked'.
    """
    import requests
    import google.auth
    from google.auth.transport.requests import AuthorizedSession, Request as GoogleAuthRequest

    quota_proj = (
        project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
    )
    source_creds = None
    default_proj = None
    try:
        source_creds, default_proj = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not quota_proj:
            quota_proj = default_proj
    except Exception:
        pass

    if source_creds and hasattr(source_creds, "service_account_email"):
        sa_creds, _ = google.auth.default(scopes=GDRIVE_SCOPES)
        sess = AuthorizedSession(sa_creds)
        if quota_proj:
            sess.headers["X-Goog-User-Project"] = quota_proj
        return sess

    if source_creds and quota_proj:
        try:
            from google.auth import impersonated_credentials
            for prefix in ("video-trimmer-sa", "meeting-transcribe-sa", "multicam-video-sa"):
                sa_email = f"{prefix}@{quota_proj}.iam.gserviceaccount.com"
                try:
                    imp_creds = impersonated_credentials.Credentials(
                        source_credentials=source_creds,
                        target_principal=sa_email,
                        target_scopes=GDRIVE_SCOPES,
                        lifetime=3600,
                    )
                    imp_creds.refresh(GoogleAuthRequest())
                    sess = AuthorizedSession(imp_creds)
                    sess.headers["X-Goog-User-Project"] = quota_proj
                    return sess
                except Exception:
                    continue
        except Exception:
            pass

    return requests.Session()


def get_gdrive_file_metadata(url_or_id: str, project_id: str = None, session=None) -> dict:
    """Query Google Drive API v3 for file metadata (id, name, mimeType, size, md5Checksum)."""
    parsed = parse_gdrive_url(url_or_id)
    file_id = parsed["id"]
    sess = session or get_gdrive_session(project_id=project_id)
    api_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {
        "fields": "id,name,mimeType,size,md5Checksum",
        "supportsAllDrives": "true",
    }
    resp = sess.get(api_url, params=params, timeout=30)
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Google Drive API Permission Error ({resp.status_code}): {resp.text}\n"
            f"Run the following command to authorize ADC for Google Drive:\n"
            f"  gcloud auth application-default login --scopes=\"https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly\""
        )
    resp.raise_for_status()
    return resp.json()


def download_gdrive_file_with_cache(
    url_or_id: str,
    target_dir: Path | str = None,
    project_id: str = None,
    force_download: bool = False,
) -> Path:
    """
    Download a Google Drive media file via ADC into `target_dir`, verifying remote MD5
    to skip re-downloading when a matching cached file already exists on disk.
    """
    parsed = parse_gdrive_url(url_or_id)
    file_id = parsed["id"]
    sess = get_gdrive_session(project_id=project_id)
    try:
        meta = get_gdrive_file_metadata(url_or_id, project_id=project_id, session=sess)
        filename = meta.get("name") or f"gdrive_{file_id}.mp4"
        remote_md5 = meta.get("md5Checksum")
        remote_size = int(meta.get("size", 0) or 0)
    except Exception:
        import requests
        from urllib.parse import unquote
        dest_dir = Path(target_dir or (Path.cwd() / "gdrive_inputs")).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dl_url = "https://drive.usercontent.google.com/download"
        with requests.get(dl_url, params={"id": file_id, "export": "download", "confirm": "t"}, stream=True, timeout=600) as r:
            r.raise_for_status()
            cd = r.headers.get("Content-Disposition", "")
            m_utf8 = re.search(r"filename\*=UTF-8''([^;]+)", cd)
            m_ascii = re.search(r'filename="([^"]+)"', cd)
            detected_name = unquote((m_utf8.group(1) if m_utf8 else (m_ascii.group(1) if m_ascii else f"gdrive_{file_id}.mp4")).strip())
            local_path = dest_dir / detected_name
            tmp_path = local_path.with_suffix(local_path.suffix + ".part")
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp_path.replace(local_path)
            return local_path

    dest_dir = Path(target_dir or (Path.cwd() / "gdrive_inputs")).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = dest_dir / filename

    if not force_download and local_path.is_file():
        if remote_md5 and local_path.stat().st_size == remote_size:
            if compute_file_md5(local_path) == remote_md5:
                print(f"[GDrive Cache Hit] Local file MD5 matches Google Drive ({local_path.name}). Skipping download.")
                return local_path

    size_mb = remote_size / (1024 * 1024)
    print(f"[*] [GDrive Download] Pulling '{filename}' ({size_mb:.1f} MB) from Google Drive via ADC...")
    dl_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
    tmp_path = local_path.with_suffix(local_path.suffix + ".part")
    with sess.get(dl_url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
    tmp_path.replace(local_path)
    print(f"[✓] Google Drive download complete: {local_path}")
    return local_path


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
    client: Any = None,
    timeout: int = 600,
    chunk_size: int = 8 * 1024 * 1024,
) -> str:
    """
    Upload a local file to a GCS bucket using resumable chunking and dynamic timeout.
    Returns the gs:// URI of the uploaded blob.
    """
    local_p = Path(local_path).resolve()
    if not local_p.is_file():
        raise FileNotFoundError(f"Local file not found: {local_p}")

    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name, chunk_size=chunk_size)

    try:
        file_sha256 = compute_file_sha256(local_p)
        file_md5 = compute_file_md5(local_p)
        file_size = local_p.stat().st_size
    except OSError:
        file_sha256 = None
        file_md5 = None
        file_size = None

    if file_sha256 and file_size is not None:
        try:
            if blob.exists():
                blob.reload()
                remote_meta = blob.metadata or {}
                if blob.size == file_size and (
                    remote_meta.get("sha256") == file_sha256
                    or remote_meta.get("gdrive_md5") == file_md5
                ):
                    gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
                    print(f"[✓] [GCS Cache Hit] Remote object hash matches ({gcs_uri}). Skipping upload!")
                    return gcs_uri
        except Exception:
            pass

    if content_type:
        blob.content_type = content_type
    if file_sha256:
        blob.metadata = {"sha256": file_sha256, "gdrive_md5": file_md5}

    file_size_mb = (file_size or 0) / (1024 * 1024)
    effective_timeout = max(timeout, int(file_size_mb * 5) + 60)

    print(
        f"[*] Uploading {local_p.name} ({file_size_mb:.1f} MB) to gs://{bucket_name}/{destination_blob_name} "
        f"(chunk_size: {chunk_size // (1024 * 1024)}MB, timeout: {effective_timeout}s)..."
    )
    try:
        blob.upload_from_filename(str(local_p), timeout=effective_timeout)
    except Exception as exc:
        err_msg = str(exc)
        if "403" in err_msg or "Forbidden" in err_msg or "AccessDeniedException" in err_msg:
            print(
                f"\n{'='*72}\n"
                f"[❌ GCS PERMISSION ERROR: 403 Forbidden]\n"
                f"Failed to upload media to Cloud Storage bucket 'gs://{bucket_name}'.\n"
                f"Your GCP identity does not have sufficient permission on this bucket.\n\n"
                f"Action Required:\n"
                f"  1. Ensure you have 'roles/storage.objectUser' on 'gs://{bucket_name}':\n"
                f"     gcloud storage buckets add-iam-policy-binding gs://{bucket_name} \\\n"
                f"       --member=\"user:$(gcloud config get-value account)\" \\\n"
                f"       --role=\"roles/storage.objectUser\"\n"
                f"  2. Or specify an accessible bucket via: --bucket <BUCKET_NAME>\n"
                f"  3. Re-authenticate if credentials expired: gcloud auth application-default login\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
        elif "RefreshError" in err_msg or "invalid_scope" in err_msg or "401" in err_msg:
            print(
                f"\n{'='*72}\n"
                f"[❌ GCP AUTHENTICATION ERROR]\n"
                f"Application Default Credentials (ADC) are invalid, unauthenticated, or expired:\n"
                f"  {exc}\n\n"
                f"Action Required:\n"
                f"  Run: gcloud auth application-default login\n"
                f"{'='*72}\n",
                file=sys.stderr,
            )
        raise

    gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
    print(f"[✓] Upload complete: {gcs_uri}")
    return gcs_uri


def delete_gcs_blob(gcs_uri: str, client: Any = None) -> None:
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
    client: Any = None,
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
    client: Any = None,
) -> str:
    """
    Generate a v4 signed URL for secure browser access and streaming.
    Defaults to 24 hours (86,400 seconds) expiration.
    """
    gcs_client = client or get_gcs_client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    try:
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=expiration_hours),
            method="GET",
        )
        return url
    except Exception as e:
        print(f"[!] Warning: generate_signed_url failed ({e}). Falling back to storage URL.")
        return f"https://storage.cloud.google.com/{bucket_name}/{blob_name}"
