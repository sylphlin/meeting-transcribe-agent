"""
scripts/chirp3_engine.py - Dual-Pass Chirp 3 Acoustic Diarization & Timestamp Engine.
Executes Track A (unchunked global speaker diarization) and Track B (parallel 18-minute
chunked word-level timestamps) concurrently via Google Cloud Speech-to-Text v2 (chirp_3),
then fuses both tracks via AlignmentEngine.
Uses Application Default Credentials (ADC) exclusively -- no API keys.
"""

import concurrent.futures
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request
import uuid

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage

from scripts.alignment_engine import (
    AlignmentEngine,
    deduplicate_overlap_micro_words,
    normalize_speaker_label,
)
from scripts.audio_utils import (
    detect_audio_speech_onset,
    format_offset,
    get_audio_duration,
    safe_ascii_upload_path,
)
from scripts.gcs_utils import (
    delete_gcs_blob,
    upload_file_to_gcs,
)


DEFAULT_CHIRP3_MODEL = "chirp_3"
DEFAULT_STT_LOCATION = "us"
TRACK_B_CHUNK_DURATION_SEC = 1080.0  # 18 minutes (< 20-minute STT v2 word offset limit)
TRACK_B_OVERLAP_SEC = 5.0            # 5-second overlap window between consecutive chunks

# Store the latest per-turn aligned word items for Stage 2 semantic paragraph re-projection
_LAST_ALIGNED_TURNS_CACHE: List[Dict[str, Any]] = []


def get_last_aligned_turns() -> List[Dict[str, Any]]:
    """Return a copy of the most recently aligned turn dicts containing word_items."""
    return list(_LAST_ALIGNED_TURNS_CACHE)


def clear_last_aligned_turns() -> None:
    """Clear the cached aligned turns."""
    _LAST_ALIGNED_TURNS_CACHE.clear()


# Official Cloud STT v2 chirp_3 locales that support speaker_diarization
CHIRP3_DIARIZATION_SUPPORTED_LANGS = {
    "auto",
    "cmn-Hans-CN",
    "de-DE",
    "en-GB",
    "en-IN",
    "en-US",
    "es-ES",
    "es-US",
    "fr-CA",
    "fr-FR",
    "hi-IN",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "pt-BR",
}

CHIRP3_DIARIZATION_FALLBACK_MAP = {
    "cmn-hant-tw": "cmn-Hans-CN",
    "yue-hant-hk": "cmn-Hans-CN",
    "en-au": "en-US",
    "en-ph": "en-US",
    "es-mx": "es-US",
    "pt-pt": "pt-BR",
}


def resolve_chirp3_language_codes(
    language: Optional[str] = None,
    for_diarization: bool = False,
) -> List[str]:
    """
    Resolve BCP-47 language codes for Chirp 3 recognition (maximum 2 codes per Cloud STT v2 limit,
    or ['auto'] for automatic language detection).
    When for_diarization=True (Track A), automatically maps locales that do not support
    speaker_diarization in Cloud STT v2 (such as cmn-Hant-TW) to their supported acoustic
    counterpart (cmn-Hans-CN) or 'auto'.
    """
    if not language or str(language).strip().lower() == "auto":
        return ["auto"]

    raw = str(language).strip().split(",")[0].strip()
    lower = raw.lower().replace("_", "-")
    lang_map = {
        "en": "en-US",
        "en-us": "en-US",
        "en-gb": "en-GB",
        "en-in": "en-IN",
        "en-au": "en-AU",
        "zh": "cmn-Hant-TW",
        "zh-tw": "cmn-Hant-TW",
        "zh-hant": "cmn-Hant-TW",
        "cmn-hant-tw": "cmn-Hant-TW",
        "zh-hk": "yue-Hant-HK",
        "yue-hant-hk": "yue-Hant-HK",
        "zh-cn": "cmn-Hans-CN",
        "zh-hans": "cmn-Hans-CN",
        "cmn-hans-cn": "cmn-Hans-CN",
        "ja": "ja-JP",
        "ja-jp": "ja-JP",
        "ko": "ko-KR",
        "ko-kr": "ko-KR",
        "de": "de-DE",
        "de-de": "de-DE",
        "fr": "fr-FR",
        "fr-fr": "fr-FR",
        "fr-ca": "fr-CA",
        "es": "es-ES",
        "es-es": "es-ES",
        "es-us": "es-US",
        "it": "it-IT",
        "it-it": "it-IT",
        "pt": "pt-BR",
        "pt-br": "pt-BR",
        "hi": "hi-IN",
        "hi-in": "hi-IN",
    }
    primary = lang_map.get(lower, raw)

    if for_diarization:
        supported_lower = {c.lower(): c for c in CHIRP3_DIARIZATION_SUPPORTED_LANGS}
        if primary.lower() in supported_lower:
            primary = supported_lower[primary.lower()]
        else:
            primary = CHIRP3_DIARIZATION_FALLBACK_MAP.get(primary.lower(), "auto")

    if primary.lower() == "auto":
        return ["auto"]

    if not primary.lower().startswith("en-"):
        return [primary, "en-US"]
    return [primary]


def get_gcp_credentials_and_project(project_id: Optional[str] = None) -> Tuple[Any, str]:
    """Obtain refreshed ADC credentials and resolve the Google Cloud project ID."""
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials, adc_project = google.auth.default(scopes=scopes)
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())

    resolved_project = (
        project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or adc_project
    )
    if not resolved_project:
        raise ValueError(
            "Missing Google Cloud project ID. Set GOOGLE_CLOUD_PROJECT in .env or pass --project."
        )
    return credentials, resolved_project


def get_stt_v2_base_url(stt_location: str) -> str:
    """Return the regional or global Cloud Speech-to-Text v2 REST endpoint base URL."""
    loc = (stt_location or DEFAULT_STT_LOCATION).strip().lower()
    if loc == "global":
        return "https://speech.googleapis.com/v2"
    return f"https://{loc}-speech.googleapis.com/v2"


def _stt_rest_request(
    method: str,
    url: str,
    credentials: Any,
    payload: Optional[Dict[str, Any]] = None,
    max_retries: int = 4,
    initial_delay: float = 2.0,
    op_name: str = "Cloud STT v2 request",
) -> Dict[str, Any]:
    """
    Execute an authenticated REST request to Cloud Speech-to-Text v2 with retry on transient errors.
    Fails fast on HTTP 401 and 403 authentication or permission errors.
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if not credentials.valid:
                credentials.refresh(GoogleAuthRequest())
            headers = {
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw_body = resp.read().decode("utf-8")
                return json.loads(raw_body) if raw_body.strip() else {}
        except urllib.error.HTTPError as e:
            status_code = e.code
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if status_code in (401, 403):
                raise RuntimeError(
                    f"{op_name} failed with HTTP {status_code} (Authentication/Permission Denied): {err_body}"
                ) from e
            if status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                last_err = e
                sleep_sec = initial_delay * (2.0 ** attempt) * (1.0 + random.uniform(-0.15, 0.15))
                print(
                    f"[!] {op_name}: HTTP {status_code}. Retrying in {sleep_sec:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(max(0.5, sleep_sec))
                continue
            raise RuntimeError(f"{op_name} failed with HTTP {status_code}: {err_body}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < max_retries - 1:
                sleep_sec = initial_delay * (2.0 ** attempt)
                print(
                    f"[!] {op_name}: network error ({e}). Retrying in {sleep_sec:.1f}s "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(max(0.5, sleep_sec))
                continue
            raise
    if last_err:
        raise last_err
    return {}


def _poll_stt_operation(
    base_url: str,
    operation_name: str,
    credentials: Any,
    poll_interval_sec: float = 2.5,
    timeout_sec: float = 900.0,
    op_label: str = "BatchRecognize",
) -> Dict[str, Any]:
    """Poll a Cloud Speech-to-Text v2 Long-Running Operation until completion."""
    op_url = f"{base_url}/{operation_name.lstrip('/')}"
    t_start = time.time()
    while True:
        op_data = _stt_rest_request("GET", op_url, credentials, op_name=f"Poll {op_label}")
        if op_data.get("done"):
            if "error" in op_data:
                err_obj = op_data["error"]
                raise RuntimeError(
                    f"{op_label} operation failed: [{err_obj.get('code')}] {err_obj.get('message')}"
                )
            return op_data
        if time.time() - t_start > timeout_sec:
            raise TimeoutError(f"{op_label} operation timed out after {timeout_sec:.0f}s.")
        time.sleep(poll_interval_sec)


def prepare_16k_mono_mp3(audio_path: Path) -> Tuple[Path, bool]:
    """
    Transcode input audio to a 16kHz mono 64kbps MP3 file for Chirp 3 ingestion.
    Returns (mp3_path, is_temporary).
    """
    temp_mp3 = Path(tempfile.gettempdir()) / f"chirp3_16k_{uuid.uuid4().hex[:8]}.mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(temp_mp3),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return temp_mp3, True


def compute_track_b_chunk_windows(
    total_duration: float,
    chunk_duration_sec: float = TRACK_B_CHUNK_DURATION_SEC,
    overlap_sec: float = TRACK_B_OVERLAP_SEC,
) -> List[Tuple[float, float]]:
    """
    Compute 18-minute chunk windows with a 5-second overlap for Track B.
    Example for a 57-minute recording:
      Chunk 0: (0.0, 1080.0)
      Chunk 1: (1075.0, 2160.0)
      Chunk 2: (2155.0, 3240.0)
      Chunk 3: (3235.0, total_duration)
    """
    if total_duration <= 0:
        return [(0.0, 0.0)]
    if total_duration <= chunk_duration_sec:
        return [(0.0, total_duration)]

    windows: List[Tuple[float, float]] = []
    idx = 0
    while True:
        start_sec = 0.0 if idx == 0 else max(0.0, idx * chunk_duration_sec - overlap_sec)
        end_sec = min(total_duration, (idx + 1) * chunk_duration_sec)
        if end_sec - start_sec < 1.0 and windows:
            # Merge tiny trailing sliver into the previous window if under 20 min limit
            prev_s, _ = windows[-1]
            if total_duration - prev_s <= 1180.0:
                windows[-1] = (prev_s, total_duration)
            break
        windows.append((round(start_sec, 3), round(end_sec, 3)))
        if end_sec >= total_duration:
            break
        idx += 1
    return windows


def _extract_words_from_stt_result_json(
    result_payload: Dict[str, Any],
    require_speaker: bool = False,
) -> List[Dict[str, Any]]:
    """
    Extract the ordered words list from a Cloud STT v2 BatchRecognize output JSON.
    """
    extracted: List[Dict[str, Any]] = []
    results_list = result_payload.get("results", [])
    for res_item in results_list:
        alternatives = res_item.get("alternatives", [])
        if not alternatives:
            continue
        best_alt = alternatives[0]
        words = best_alt.get("words", [])
        if words:
            for w in words:
                word_str = str(w.get("word", "")).strip()
                if not word_str:
                    continue
                entry: Dict[str, Any] = {"word": word_str}
                if "speakerLabel" in w or require_speaker:
                    entry["speakerLabel"] = normalize_speaker_label(w.get("speakerLabel", "1"))
                if "startOffset" in w:
                    entry["startOffset"] = w["startOffset"]
                if "endOffset" in w:
                    entry["endOffset"] = w["endOffset"]
                extracted.append(entry)
        elif best_alt.get("transcript"):
            # Fallback if a result block has transcript text without word entries
            raw_transcript = str(best_alt.get("transcript", "")).strip()
            spk_label = normalize_speaker_label(res_item.get("speakerLabel", "1"))
            for tok in raw_transcript.split():
                entry = {"word": tok}
                if require_speaker:
                    entry["speakerLabel"] = spk_label
                extracted.append(entry)
    return extracted


def _fetch_batch_recognize_output_json(
    op_data: Dict[str, Any],
    bucket_name: str,
    gcs_output_prefix: str,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve parsed BatchRecognize result JSON payloads from either the operation's
    cloudStorageResult URI, inlineResult, or by listing blobs under gcs_output_prefix.
    """
    payloads: List[Dict[str, Any]] = []
    resp_results = op_data.get("response", {}).get("results", {})
    gcs_uris_to_read: List[str] = []

    if isinstance(resp_results, dict):
        for _file_uri, file_res in resp_results.items():
            if not isinstance(file_res, dict):
                continue
            if "error" in file_res and file_res["error"].get("code", 0) != 0:
                err_info = file_res["error"]
                raise RuntimeError(
                    f"BatchRecognize file error: [{err_info.get('code')}] {err_info.get('message')}"
                )
            inline_tx = file_res.get("inlineResult", {}).get("transcript")
            if isinstance(inline_tx, dict) and "results" in inline_tx:
                payloads.append(inline_tx)
                continue
            cs_uri = (
                file_res.get("cloudStorageResult", {}).get("uri")
                or file_res.get("uri")
            )
            if cs_uri:
                gcs_uris_to_read.append(cs_uri)

    if payloads:
        return payloads

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    if gcs_uris_to_read:
        for uri in gcs_uris_to_read:
            prefix_clean = f"gs://{bucket_name}/"
            if uri.startswith(prefix_clean):
                blob_name = uri[len(prefix_clean):]
                blob = bucket.blob(blob_name)
                if blob.exists():
                    payloads.append(json.loads(blob.download_as_text(encoding="utf-8")))
        if payloads:
            return payloads

    # Fallback: list all JSON blobs under gcs_output_prefix
    clean_prefix = gcs_output_prefix.removeprefix(f"gs://{bucket_name}/").lstrip("/")
    for blob in storage_client.list_blobs(bucket_name, prefix=clean_prefix):
        if blob.name.endswith(".json"):
            payloads.append(json.loads(blob.download_as_text(encoding="utf-8")))

    return payloads


def _cleanup_gcs_prefix(bucket_name: str, prefix: str, project_id: Optional[str] = None) -> None:
    """Delete all temporary GCS objects under a given prefix."""
    try:
        storage_client = storage.Client(project=project_id)
        clean_prefix = prefix.removeprefix(f"gs://{bucket_name}/").lstrip("/")
        blobs = list(storage_client.list_blobs(bucket_name, prefix=clean_prefix))
        for blob in blobs:
            try:
                blob.delete()
            except Exception:
                pass
    except Exception:
        pass


def run_track_a_macro(
    mp3_path: Path,
    bucket_name: str,
    run_id: str,
    project_id: str,
    stt_location: str,
    credentials: Any,
    language_codes: List[str],
    model_name: str = DEFAULT_CHIRP3_MODEL,
    min_speakers: int = 2,
    max_speakers: int = 10,
) -> List[Dict[str, Any]]:
    """
    Execute Track A (Macro Global Diarization Track) on the unchunked full audio file.
    Sets enableWordTimeOffsets=False and diarizationConfig(min, max) to obtain globally
    consistent Speaker 1..N labels across the entire meeting.
    """
    t0 = time.time()
    blob_name = f"raw/chirp3_{run_id}/audio_16k_full.mp3"
    output_prefix = f"gs://{bucket_name}/raw/chirp3_{run_id}/results_macro/"

    with safe_ascii_upload_path(mp3_path) as safe_path:
        print(f"[*] [Track A - Macro] Uploading full unchunked audio to gs://{bucket_name}/{blob_name}...")
        full_gcs_uri = upload_file_to_gcs(
            local_path=safe_path,
            bucket_name=bucket_name,
            destination_blob_name=blob_name,
            content_type="audio/mpeg",
        )

    base_url = get_stt_v2_base_url(stt_location)
    recognize_url = f"{base_url}/projects/{project_id}/locations/{stt_location}/recognizers/_:batchRecognize"
    payload = {
        "config": {
            "model": model_name,
            "languageCodes": language_codes,
            "features": {
                "diarizationConfig": {
                    "minSpeakerCount": max(1, min_speakers),
                    "maxSpeakerCount": max(min_speakers, max_speakers),
                }
            },
            "autoDecodingConfig": {},
        },
        "files": [{"uri": full_gcs_uri}],
        "recognitionOutputConfig": {
            "gcsOutputConfig": {"uri": output_prefix}
        },
    }

    print(
        f"[*] [Track A - Macro] Submitting unchunked global diarization job (`{model_name}`, "
        f"speakers={min_speakers}..{max_speakers}, enableWordTimeOffsets=False)..."
    )
    op_init = _stt_rest_request("POST", recognize_url, credentials, payload=payload, op_name="Track A BatchRecognize")
    op_name = op_init.get("name", "")
    if not op_name:
        raise RuntimeError(f"Track A BatchRecognize did not return an operation name: {op_init}")

    op_done = _poll_stt_operation(base_url, op_name, credentials, op_label="Track A Macro Diarization")
    json_payloads = _fetch_batch_recognize_output_json(op_done, bucket_name, output_prefix, project_id=project_id)

    macro_words: List[Dict[str, Any]] = []
    for jp in json_payloads:
        macro_words.extend(_extract_words_from_stt_result_json(jp, require_speaker=True))

    elapsed = time.time() - t0
    unique_speakers = sorted({w.get("speakerLabel", "Speaker 1") for w in macro_words})
    print(
        f"[✓] [Track A - Macro] Global diarization finished in {elapsed:.1f}s "
        f"({len(macro_words)} words, {len(unique_speakers)} global speakers: {', '.join(unique_speakers[:8])})."
    )
    return macro_words


def run_track_b_micro(
    mp3_path: Path,
    total_duration: float,
    bucket_name: str,
    run_id: str,
    project_id: str,
    stt_location: str,
    credentials: Any,
    language_codes: List[str],
    model_name: str = DEFAULT_CHIRP3_MODEL,
) -> List[Dict[str, Any]]:
    """
    Execute Track B (Micro Timestamp Track) in parallel 18-minute chunks with 5s overlap.
    Sets enableWordTimeOffsets=True to extract accurate word-level [startOffset, endOffset].
    """
    t0 = time.time()
    windows = compute_track_b_chunk_windows(total_duration)
    num_chunks = len(windows)
    base_url = get_stt_v2_base_url(stt_location)
    recognize_url = f"{base_url}/projects/{project_id}/locations/{stt_location}/recognizers/_:batchRecognize"

    print(
        f"[*] [Track B - Micro] Dispatching {num_chunks} parallel chunk(s) "
        f"(`{model_name}`, enableWordTimeOffsets=True)..."
    )

    def _process_micro_chunk(chunk_idx: int) -> Tuple[int, float, float, List[Dict[str, Any]]]:
        c_start, c_end = windows[chunk_idx]
        c_dur = c_end - c_start
        temp_chunk: Optional[Path] = None
        chunk_blob_name = f"raw/chirp3_{run_id}/chunks/chunk_{chunk_idx:02d}.mp3"
        output_prefix = f"gs://{bucket_name}/raw/chirp3_{run_id}/results_micro/chunk_{chunk_idx:02d}/"

        try:
            if num_chunks == 1 and c_start == 0.0:
                upload_source = mp3_path
            else:
                temp_chunk = Path(tempfile.gettempdir()) / f"chirp3_chunk_{run_id}_{chunk_idx:02d}.mp3"
                slice_cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(c_start),
                    "-t",
                    str(c_dur),
                    "-i",
                    str(mp3_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    str(temp_chunk),
                ]
                subprocess.run(slice_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                upload_source = temp_chunk

            with safe_ascii_upload_path(upload_source) as safe_chunk_path:
                chunk_gcs_uri = upload_file_to_gcs(
                    local_path=safe_chunk_path,
                    bucket_name=bucket_name,
                    destination_blob_name=chunk_blob_name,
                    content_type="audio/mpeg",
                )

            payload = {
                "config": {
                    "model": model_name,
                    "languageCodes": language_codes,
                    "features": {
                        "enableWordTimeOffsets": True,
                    },
                    "autoDecodingConfig": {},
                },
                "files": [{"uri": chunk_gcs_uri}],
                "recognitionOutputConfig": {
                    "gcsOutputConfig": {"uri": output_prefix}
                },
            }

            op_init = _stt_rest_request(
                "POST",
                recognize_url,
                credentials,
                payload=payload,
                op_name=f"Track B Chunk {chunk_idx + 1}/{num_chunks}",
            )
            op_name = op_init.get("name", "")
            if not op_name:
                raise RuntimeError(f"Track B chunk {chunk_idx} did not return operation name: {op_init}")

            op_done = _poll_stt_operation(
                base_url,
                op_name,
                credentials,
                op_label=f"Track B Micro Chunk {chunk_idx + 1}/{num_chunks}",
            )
            json_payloads = _fetch_batch_recognize_output_json(
                op_done, bucket_name, output_prefix, project_id=project_id
            )

            chunk_words: List[Dict[str, Any]] = []
            for jp in json_payloads:
                chunk_words.extend(_extract_words_from_stt_result_json(jp, require_speaker=False))

            print(
                f"[✓] [Track B - Micro] Chunk {chunk_idx + 1}/{num_chunks} "
                f"[{format_offset(c_start)} - {format_offset(c_end)}] complete ({len(chunk_words)} timed words)."
            )
            return chunk_idx, c_start, c_end, chunk_words
        finally:
            if temp_chunk and temp_chunk.exists():
                try:
                    temp_chunk.unlink()
                except Exception:
                    pass

    max_workers = min(max(1, num_chunks), 6)
    results: List[Tuple[int, float, float, List[Dict[str, Any]]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_micro_chunk, idx) for idx in range(num_chunks)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda x: x[0])
    chunk_word_tuples = [(c_start, c_end, c_words) for _, c_start, c_end, c_words in results]
    merged_micro_words = deduplicate_overlap_micro_words(chunk_word_tuples)

    elapsed = time.time() - t0
    print(
        f"[✓] [Track B - Micro] Parallel timestamp extraction finished in {elapsed:.1f}s "
        f"({len(merged_micro_words)} monotonic word timestamps)."
    )
    return merged_micro_words


def validate_chirp_model(model_name: Optional[str] = None) -> str:
    """
    Resolve and validate the Cloud Speech-to-Text v2 model name.
    Enforces that only Chirp-series models (for example, 'chirp_3') are allowed.
    """
    resolved = (model_name or os.environ.get("TRANSCRIBE_MODEL") or DEFAULT_CHIRP3_MODEL).strip()
    if not resolved.lower().startswith("chirp"):
        raise ValueError(
            f"Invalid TRANSCRIBE_MODEL '{resolved}'. "
            f"Stage 1 Cloud Speech-to-Text v2 only supports Chirp-series models (for example, '{DEFAULT_CHIRP3_MODEL}')."
        )
    return resolved


def transcribe_with_chirp3_dual_pass(
    audio_path: Path,
    bucket_name: str,
    project_id: Optional[str] = None,
    stt_location: Optional[str] = None,
    model_name: Optional[str] = None,
    language: Optional[str] = "auto",
    min_speakers: int = 2,
    max_speakers: int = 10,
) -> Tuple[str, float]:
    """
    Execute the Dual-Pass Chirp 3 Acoustic Diarization & Multilingual Alignment Pipeline:
      1. Transcode source audio to 16kHz mono MP3.
      2. Concurrently launch Track A (Macro Global Diarization, unchunked) and
         Track B (Micro Word Timestamps, parallel 18-minute chunks).
      3. Run AlignmentEngine to suppress repetition loops, project word-level timestamps
         onto global speaker words, and aggregate speaker turns.
      4. Cache per-turn word timestamps for Stage 2 LLM semantic paragraph re-projection.
      5. Clean up all temporary GCS artifacts under gs://<bucket>/raw/chirp3_<run_id>/.
    """
    t0 = time.time()
    clear_last_aligned_turns()

    if not bucket_name:
        raise ValueError(
            "A Cloud Storage bucket is required for Dual-Pass Chirp 3 transcription. "
            "Pass --bucket or set MEETING_STORAGE_BUCKET in .env."
        )

    resolved_model = validate_chirp_model(model_name)

    resolved_stt_loc = (
        stt_location
        or os.environ.get("STT_LOCATION")
        or DEFAULT_STT_LOCATION
    )
    credentials, resolved_project = get_gcp_credentials_and_project(project_id)
    track_a_lang_codes = resolve_chirp3_language_codes(language, for_diarization=True)
    track_b_lang_codes = resolve_chirp3_language_codes(language, for_diarization=False)
    prefer_micro_cjk = (track_a_lang_codes != track_b_lang_codes)
    run_id = uuid.uuid4().hex[:10]
    run_gcs_prefix = f"raw/chirp3_{run_id}/"

    temp_mp3: Optional[Path] = None
    is_temp = False

    try:
        print(f"[*] Preparing 16kHz mono MP3 stream for Dual-Pass `{resolved_model}`...")
        temp_mp3, is_temp = prepare_16k_mono_mp3(audio_path)
        total_duration = get_audio_duration(temp_mp3)
        print(
            f"[*] Audio ready ({format_offset(total_duration)}). Launching concurrent Dual-Pass "
            f"`{resolved_model}` (Location: {resolved_stt_loc}, "
            f"Track A Langs: {', '.join(track_a_lang_codes)} | Track B Langs: {', '.join(track_b_lang_codes)})..."
        )

        # Launch Track A and Track B concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_macro = executor.submit(
                run_track_a_macro,
                mp3_path=temp_mp3,
                bucket_name=bucket_name,
                run_id=run_id,
                project_id=resolved_project,
                stt_location=resolved_stt_loc,
                credentials=credentials,
                language_codes=track_a_lang_codes,
                model_name=resolved_model,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            fut_micro = executor.submit(
                run_track_b_micro,
                mp3_path=temp_mp3,
                total_duration=total_duration,
                bucket_name=bucket_name,
                run_id=run_id,
                project_id=resolved_project,
                stt_location=resolved_stt_loc,
                credentials=credentials,
                language_codes=track_b_lang_codes,
                model_name=resolved_model,
            )

            macro_words = fut_macro.result()
            micro_words = fut_micro.result()

        print("[*] Running Multilingual LCS Alignment & Fusion Engine (Track B physical timeline as master)...")
        physical_onset = detect_audio_speech_onset(temp_mp3)
        if physical_onset > 0.0:
            print(f"[*] Physical speech onset detected at {format_offset(physical_onset, mode='floor')} ({physical_onset:.2f}s). Earlier tokens are discarded.")

        aligner = AlignmentEngine(segment_base_seconds=0.0)
        clean_macro_words = aligner.suppress_repetition_loops(macro_words)
        if len(clean_macro_words) < len(macro_words):
            removed = len(macro_words) - len(clean_macro_words)
            print(f"[*] Suppressed {removed} repetitive loop tokens from Track A stream.")
        clean_micro_words = aligner.suppress_repetition_loops(micro_words)
        if len(clean_micro_words) < len(micro_words):
            removed = len(micro_words) - len(clean_micro_words)
            print(f"[*] Suppressed {removed} repetitive loop tokens from Track B stream.")

        aligned_words = aligner.project_timestamps(
            clean_macro_words,
            clean_micro_words,
            prefer_micro_cjk=prefer_micro_cjk,
            physical_onset_sec=physical_onset,
        )
        turns = aligner.aggregate_turns(aligned_words)

        _LAST_ALIGNED_TURNS_CACHE.extend(turns)

        formatted_lines: List[str] = []
        for turn in turns:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            start_str = format_offset(turn.get("start") or 0.0, mode="floor")
            end_str = format_offset(turn.get("end") or 0.0, mode="ceil")
            spk = turn.get("speaker") or "Speaker 1"
            formatted_lines.append(f"[{start_str} - {end_str}] **{spk}**: {text}")

        raw_transcript_text = "\n\n".join(formatted_lines)
        elapsed = time.time() - t0
        print(
            f"[*] ✓ Dual-Pass Chirp 3 transcription & alignment complete in {elapsed:.1f}s "
            f"({len(formatted_lines)} speaker turns, {len(raw_transcript_text)} chars)."
        )
        return raw_transcript_text, elapsed

    finally:
        print(f"[*] Cleaning up ephemeral Cloud Storage artifacts under gs://{bucket_name}/{run_gcs_prefix}...")
        _cleanup_gcs_prefix(bucket_name, run_gcs_prefix, project_id=resolved_project)
        if is_temp and temp_mp3 and temp_mp3.exists():
            try:
                temp_mp3.unlink()
            except Exception:
                pass
