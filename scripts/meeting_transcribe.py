#!/usr/bin/env python3
"""
scripts/meeting_transcribe.py - Main Pipeline Orchestrator for Meeting Transcribe Agent.
Universal Cloud-Scale Intelligence (gemini-3.5-transcribe-preview) with Offline Whisper Backup.
Uses Vertex AI with Application Default Credentials exclusively -- no AI Studio API key.
"""

import os
import sys
import time
import argparse
from pathlib import Path
from typing import Optional

# Add project root to sys.path
root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from scripts.audio_utils import (
    compress_audio_for_upload,
    get_audio_duration,
    format_offset,
    is_youtube_url,
    extract_youtube_id,
    is_video_file,
    extract_audio_from_video,
    detect_embedded_subtitles,
    extract_embedded_subtitles,
    fix_mojibake_filename,
)
from scripts.diarization import transcribe_with_local_whisper_and_diarization
from scripts.gemini_engine import (
    get_gemini_client,
    load_env_file,
    transcribe_with_gemini_cloud,
    generate_minutes_with_gemini,
    process_video_meeting_end_to_end,
    analyze_video_with_transcript,
)
from scripts.glossary import (
    extract_global_consistency_glossary,
    extract_keywords_from_glossary,
    extract_detected_language_from_glossary,
)
from scripts.canonicalizer import (
    consolidate_meeting_minutes,
    generate_auto_outline_from_srt,
)
from scripts.html_generator import generate_interactive_html


def generate_meeting_minutes_and_transcript(
    input_source: str,
    output_file: str = None,
    engine: str = "gemini",
    whisper_backend: str = "auto",
    whisper_model: str = "small",
    enable_diarization: bool = True,
    clustering_threshold: float = 0.68,
    num_speakers: int = -1,
    embedding_type: str = "eres2net",
    project_id: str = None,
    location: str = None,
    bucket_name: str = None,
    transcribe_model: str = None,
    summary_model: str = None,
    outline: str = None,
    force_glossary: bool = False,
    no_glossary: bool = False,
    no_player: bool = False,
    compress: bool = True,
    summary_language: str = None,
    only_transcript: bool = False,
    language: str | None = "auto",
    agentic: bool = True,
    extract_audio: bool = False,
    serve: bool = False,
) -> Path:

    """
    End-to-End Meeting Transcription & Intelligence Pipeline:
    - Pipeline 1 (YouTube Multimodal): Direct cloud ingestion via Gemini Multimodal Vision with native Agentic Video Understanding.
    - Pipeline 2 (Local Video Two-Stage Fusion): Stage 1 16kHz Audio Extraction + Shared Acoustic ASR (Gemini 3.5 Transcribe or offline Whisper), Stage 2 Gemini 3.8 Flash Native Agentic Multimodal Vision Fusion (slides, faces, nameplates) & minutes synthesis, followed by deterministic assembly & synchronized video player.
    - Pipeline 3 (Pure Audio): Shared Stage 1 Acoustic ASR (Gemini 3.5 Transcribe with ephemeral Cloud Storage auto-cleanup or offline Whisper + Diarization) + Stage 2 Gemini 3.8 Flash minutes structuring.

    Media fed to Gemini (local audio/video, not YouTube URLs) is staged through a
    Cloud Storage bucket (bucket_name, or MEETING_STORAGE_BUCKET in the
    environment) -- Vertex AI has no equivalent of the old Files API, so it reads
    uploads via a gs:// URI instead.
    """
    load_env_file()
    transcribe_model = transcribe_model or os.environ.get("TRANSCRIBE_MODEL") or "gemini-3.5-transcribe-preview"
    summary_model = summary_model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
    from scripts.gcs_utils import is_gdrive_source, download_gdrive_file_with_cache
    source_str = str(input_source).strip()
    if is_gdrive_source(source_str):
        target_dl_dir = Path(output_file).resolve().parent / "gdrive_inputs" if output_file else Path.cwd() / "gdrive_inputs"
        local_gdrive_path = download_gdrive_file_with_cache(
            source_str,
            target_dir=target_dl_dir,
            project_id=project_id,
        )
        source_str = str(local_gdrive_path)
    is_yt = is_youtube_url(source_str)
    is_vid = is_video_file(source_str) if not is_yt else False
    resolved_bucket = (bucket_name or os.environ.get("MEETING_STORAGE_BUCKET", "")).removeprefix("gs://") or None

    # Branch 1: YouTube Multimodal Cloud Pipeline (Direct Ingestion)
    if is_yt and not extract_audio:
        print(f"\n========================================================")
        print(f"🎥  Meeting Transcribe Agent: YouTube Multimodal Pipeline")
        print(f"📺  Source: {source_str}")
        print(f"🤖  Mode: 🤖 Agentic Video Understanding (Dynamic frame navigation & tool-use)")
        print(f"⚙️  Vision Model: {summary_model}")
        print(f"========================================================\n")

        t_total_start = time.time()
        client = get_gemini_client(project_id=project_id, location=location)

        from scripts.audio_utils import fetch_youtube_title, sanitize_filename
        yt_id = extract_youtube_id(source_str) or "youtube_meeting"
        play_media = source_str
        yt_title = fetch_youtube_title(source_str)
        if yt_title:
            default_out_stem = sanitize_filename(yt_title)
        else:
            default_out_stem = f"yt_{yt_id}"
        out_parent = Path.cwd()

        final_markdown, video_time = process_video_meeting_end_to_end(
            client=client,
            video_source=source_str,
            bucket_name=resolved_bucket,
            summary_model=summary_model,
            use_agentic=agentic,
            summary_language=summary_language,
            outline_path=Path(outline) if outline else None,
        )

        total_time = time.time() - t_total_start

        if output_file:
            out_path = Path(output_file).resolve()
        else:
            from scripts.html_generator import extract_meeting_title
            from scripts.audio_utils import sanitize_filename
            md_title = extract_meeting_title(final_markdown)
            if md_title:
                clean_title_stem = sanitize_filename(md_title)
                if clean_title_stem:
                    default_out_stem = clean_title_stem
            out_path = out_parent / f"{default_out_stem}_minutes.md"

        out_path.write_text(final_markdown, encoding="utf-8")
        print(f"\n========================================================")
        print(f"✅ Multimodal YouTube meeting minutes successfully generated!")
        print(f"📄 Output file: {out_path.resolve()}")
        print(f"⏱️  Processing Time: {video_time:.1f}s | Total Time: {total_time:.1f}s")
        print(f"========================================================\n")

        if not no_player:
            if out_path.stem.endswith("_minutes"):
                player_path = out_path.parent / f"{out_path.stem[:-8]}_player.html"
            else:
                player_path = out_path.parent / f"{out_path.stem}_player.html"
            try:
                generate_interactive_html(
                    media_source=play_media,
                    markdown_content=final_markdown,
                    output_html_path=player_path
                )
                if serve and player_path.exists():
                    from scripts.html_generator import serve_html_player
                    serve_html_player(player_path)
            except Exception as e:
                print(f"[!] Warning: Interactive HTML player generation failed ({e}).")

        return out_path

    # Branch 2 & 3: Local Video & Audio Pipelines (Unified Stage 1 ASR Core)
    is_local_video = is_vid and not extract_audio
    subtitles_path: Optional[Path] = None

    if is_vid:
        video_p = Path(source_str).resolve()
        if not video_p.exists():
            raise FileNotFoundError(f"Video file not found: {video_p}")
        # Extract 16kHz mono audio track for Stage 1 ASR (uses cached audio if already extracted)
        audio_path = extract_audio_from_video(video_p)
        play_media = video_p if is_local_video else audio_path
        default_out_stem = fix_mojibake_filename(video_p.stem)
        out_parent = video_p.parent

        # Check for embedded WebRTC/captions stream to use as speaker ground truth
        detected_codec = detect_embedded_subtitles(video_p)
        if detected_codec:
            print(f"[*] Detected embedded subtitles stream (codec: {detected_codec}). Extracting metadata ground truth...")
            scratch_dir = out_parent / "scratch"
            scratch_dir.mkdir(parents=True, exist_ok=True)
            srt_candidate = scratch_dir / f"{default_out_stem}_embedded.srt"
            subtitles_path = extract_embedded_subtitles(video_p, srt_candidate)
            if subtitles_path and subtitles_path.exists():
                print(f"[✓] Embedded subtitles successfully extracted: {subtitles_path.name}")
                if not outline:
                    auto_outline_path = scratch_dir / f"{default_out_stem}_auto_outline.md"
                    try:
                        generate_auto_outline_from_srt(subtitles_path, auto_outline_path)
                        outline = auto_outline_path
                        print(f"[✓] Auto-generated meeting agenda outline from subtitles: {auto_outline_path.name}")
                    except Exception as e:
                        print(f"[!] Warning: Could not generate auto-outline from subtitles ({e}).")
    elif is_yt and extract_audio:
        raise ValueError("Cannot extract local audio from YouTube URL directly. Please use default YouTube Multimodal mode.")
    else:
        audio_path = Path(source_str).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        play_media = audio_path
        default_out_stem = fix_mojibake_filename(audio_path.stem)
        out_parent = audio_path.parent

    # Detect sidecar SRT if no embedded subtitles were extracted
    if not subtitles_path:
        sidecar_srt = out_parent / f"{default_out_stem}.srt"
        if sidecar_srt.is_file():
            subtitles_path = sidecar_srt
            print(f"[*] Found sidecar subtitles file: {sidecar_srt.name}")

    print(f"\n========================================================")
    if is_local_video:
        print(f"🎥  Meeting Transcribe Agent: Two-Stage Local Video Pipeline")
        print(f"📺  Source Video: {fix_mojibake_filename(video_p.name)}")
        print(f"🎙️  Stage 1 (Acoustic ASR): {transcribe_model if engine.lower() != 'whisper' else f'Whisper ({whisper_backend})'}")
        print(f"👁️  Stage 2 (Multimodal Vision Fusion): {summary_model} (Agentic)")
    else:
        print(f"🎙️  Meeting Transcribe Agent: Pure Audio Pipeline: {fix_mojibake_filename(audio_path.name)}")
        print(f"⚙️  Primary Engine: {engine.upper()} | Summary Model: {summary_model}")
        if engine.lower() == "whisper":
            print(f"   [Offline Setup] Backend: {whisper_backend} | Model: {whisper_model} | Diarization: {enable_diarization}")
        else:
            print(f"   [Cloud Setup] Model: {transcribe_model} (Ephemeral Cloud Storage upload, auto-cleaned)")
    if resolved_bucket:
        print(f"☁️  Cloud Storage Staging: gs://{resolved_bucket}")
    print(f"========================================================\n")

    t_total_start = time.time()

    # Step 0: Initialize Gemini Client
    client = None
    try:
        client = get_gemini_client(project_id=project_id, location=location)
        if engine.lower() == "gemini" and not resolved_bucket:
            raise ValueError(
                "A Cloud Storage bucket is required to process audio/video with Gemini via Vertex AI. "
                "Pass bucket_name / --bucket, or set MEETING_STORAGE_BUCKET."
            )
    except Exception as e:
        if engine.lower() == "gemini":
            raise e
        print(f"[*] Note: Gemini API not available ({e}). Operating in pure offline mode.")

    # Step 1.5: Dual-Track Global Consistency Glossary
    global_glossary = ""
    glossary_keywords = ""
    detected_lang_code = None
    if not no_glossary and client is not None:
        print("--- [Track 1 & 2] Global Terminology & Entity Mining ---")
        try:
            global_glossary, _ = extract_global_consistency_glossary(
                client=client,
                audio_path=audio_path,
                bucket_name=resolved_bucket,
                outline_path=outline,
                model=summary_model,
                force=force_glossary,
                compress_fn=compress_audio_for_upload if compress else None
            )
            if global_glossary:
                glossary_keywords = extract_keywords_from_glossary(global_glossary)
                detected_lang_code = extract_detected_language_from_glossary(global_glossary)
                if detected_lang_code:
                    print(f"[*] Detected Primary Spoken Language Code from Glossary: `{detected_lang_code}`")
        except Exception as e:
            print(f"[!] Warning: Global glossary extraction failed ({e}), proceeding with standard pipeline.")

    effective_asr_language = (
        language
        if (language and language.lower() != "auto")
        else (detected_lang_code or "auto")
    )

    # Step 1: Speech-to-Text Transcription & Acoustic Diarization (Shared Stage 1 ASR Core)
    if engine.lower() == "whisper":
        print(f"--- [Stage 1/2] Local Whisper + Sherpa-ONNX Diarization ({whisper_backend}) ---")
        raw_transcript_text, asr_time = transcribe_with_local_whisper_and_diarization(
            audio_path=audio_path,
            model_size=whisper_model,
            enable_diarization=enable_diarization,
            clustering_threshold=clustering_threshold,
            num_speakers=num_speakers,
            embedding_type=embedding_type,
            whisper_backend=whisper_backend,
            initial_prompt=glossary_keywords,
            language=language
        )
    else:
        print(f"--- [Stage 1/2] Cloud Gemini Transcribe ({transcribe_model}) ---")
        raw_transcript_text, asr_time = transcribe_with_gemini_cloud(
            client=client,
            audio_path=audio_path,
            bucket_name=resolved_bucket,
            model_name=transcribe_model,
            compress=compress,
            language=effective_asr_language
        )

    # Early return if only verbatim transcript requested
    if only_transcript:
        duration_sec = get_audio_duration(audio_path)
        dur_str = format_offset(duration_sec)
        eng_label = f"Local Whisper ({whisper_backend}/{whisper_model})" if engine.lower() == "whisper" else transcribe_model
        out_path = Path(output_file) if output_file else out_parent / f"{default_out_stem}_transcript.md"
        source_label = f"**Video File**: `{fix_mojibake_filename(video_p.name)}`" if is_local_video else f"**Audio File**: `{fix_mojibake_filename(audio_path.name)}`"
        content = (
            f"# Meeting Transcript: {default_out_stem}\n\n"
            f"- {source_label}\n"
            f"- **Duration**: {dur_str}\n"
            f"- **Transcription Engine**: {eng_label}\n"
            f"- **Generated At**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 🎙️ Verbatim Transcript\n\n"
            f"{raw_transcript_text.strip()}\n"
        )
        out_path.write_text(content, encoding="utf-8")
        print(f"\n========================================================")
        print(f"✅ Verbatim transcript successfully generated!")
        print(f"📄 Output file: {out_path.resolve()}")
        print(f"⏱️  ASR Time: {asr_time:.1f}s | Total Time: {time.time() - t_total_start:.1f}s")
        print(f"[Agent Notice] Verbatim transcript ready for Agent-native Stage 2 structuring.")
        print(f"========================================================\n")
        return out_path

    # Step 2: Stage 2 Minutes Structuring (Video Multimodal Vision Fusion vs Audio Semantic Restructuring)
    summary_time = 0.0
    final_markdown = ""

    if client is not None:
        if is_local_video:
            print(f"\n--- [Stage 2/2] Multimodal Vision & Minutes Fusion ({summary_model} Agentic) ---")
            try:
                final_markdown, summary_time = analyze_video_with_transcript(
                    client=client,
                    video_path=video_p,
                    raw_transcript_text=raw_transcript_text,
                    bucket_name=resolved_bucket,
                    summary_model=summary_model,
                    use_agentic=agentic,
                    summary_language=summary_language,
                    outline_path=Path(outline) if outline else None,
                    srt_path=subtitles_path,
                )
            except Exception as e:
                print(f"[!] Warning: Multimodal video fusion failed ({e}). Falling back to standalone acoustic transcript.")
                final_markdown = ""
        else:
            print(f"\n--- [Stage 2/2] Meeting Minutes Structuring & Speaker Role Arbitration ({summary_model}) ---")
            try:
                final_markdown, summary_time = generate_minutes_with_gemini(
                    client=client,
                    audio_path=audio_path,
                    raw_transcript_text=raw_transcript_text,
                    global_glossary=global_glossary,
                    summary_model=summary_model,
                    summary_language=summary_language,
                    srt_path=subtitles_path,
                )
            except Exception as e:
                print(f"[!] Warning: Gemini minutes structuring unavailable ({e}). Falling back to standalone verbatim transcript.")
                final_markdown = ""

    if not final_markdown:
        # Standalone verbatim report when offline or Gemini API unavailable
        duration_sec = get_audio_duration(audio_path)
        dur_str = format_offset(duration_sec)
        eng_label = f"Local Whisper ({whisper_backend}/{whisper_model})" if engine.lower() == "whisper" else transcribe_model
        source_label = f"**Video File**: `{video_p.name}`" if is_local_video else f"**Audio File**: `{audio_path.name}`"
        sec6_heading = "## 6. Full Verbatim Transcript"
        final_markdown = (
            f"# Meeting Minutes & Transcript: {default_out_stem}\n\n"
            f"- {source_label}\n"
            f"- **Duration**: {dur_str}\n"
            f"- **Transcription Engine**: {eng_label}\n"
            f"- **Generated At**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{sec6_heading}\n\n"
            f"{raw_transcript_text.strip()}\n"
        )
        final_markdown = consolidate_meeting_minutes(final_markdown, srt_path=subtitles_path)

    total_time = time.time() - t_total_start

    # Save Markdown Output
    if output_file:
        out_path = Path(output_file).resolve()
    else:
        from scripts.html_generator import extract_meeting_title
        from scripts.audio_utils import sanitize_filename
        md_title = extract_meeting_title(final_markdown)
        if md_title:
            clean_title_stem = sanitize_filename(md_title)
            if clean_title_stem:
                default_out_stem = clean_title_stem
        out_path = out_parent / f"{default_out_stem}_minutes.md"

    out_path.write_text(final_markdown, encoding="utf-8")
    print(f"\n========================================================")
    if is_local_video:
        print(f"✅ Video meeting minutes & synchronized transcript successfully generated!")
        print(f"📄 Output file: {out_path.resolve()}")
        print(f"⏱️  ASR Time: {asr_time:.1f}s | Vision Fusion Time: {summary_time:.1f}s | Total Time: {total_time:.1f}s")
    else:
        print(f"✅ Meeting transcript/minutes successfully generated!")
        print(f"📄 Output file: {out_path.resolve()}")
        print(f"⏱️  ASR Time: {asr_time:.1f}s | Summary Time: {summary_time:.1f}s | Total Time: {total_time:.1f}s")
    print(f"========================================================\n")

    # Step 3: Interactive HTML Player Generation
    if not no_player:
        if out_path.stem.endswith("_minutes"):
            player_path = out_path.parent / f"{out_path.stem[:-8]}_player.html"
        else:
            player_path = out_path.parent / f"{out_path.stem}_player.html"
        try:
            generate_interactive_html(
                media_source=play_media,
                markdown_content=final_markdown,
                output_html_path=player_path
            )
            if serve and player_path.exists():
                from scripts.html_generator import serve_html_player
                serve_html_player(player_path)
        except Exception as e:
            print(f"[!] Warning: Interactive HTML player generation failed ({e}).")

    return out_path


def main():
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Meeting Transcribe Agent - Universal Cloud-Scale Intelligence & Offline Whisper Backup Suite"
    )
    parser.add_argument("input_source", help="Path to audio/video file (mp3, m4a, wav, mp4, mov, mkv, etc.), Google Drive link (https://drive.google.com/... / gdrive://...), or YouTube URL")
    parser.add_argument("-o", "--output", help="Path to output Markdown file (default: <filename>_minutes.md)")

    parser.add_argument(
        "--agentic",
        action="store_true",
        default=True,
        help="Agentic Video Understanding (dynamic frame navigation & tool-use) is natively enabled by default for all video sources"
    )
    parser.add_argument(
        "--extract-audio",
        action="store_true",
        help="Force extracting audio track from video files and routing to the pure audio pipeline"
    )
    parser.add_argument(
        "--engine",
        choices=["gemini", "whisper"],
        default="gemini",
        help="Transcription engine for audio: 'gemini' (default, cloud Gemini 3.5 Transcribe) or 'whisper' (local offline backup)"
    )
    parser.add_argument(
        "--whisper-backend",
        choices=["auto", "mlx", "faster-whisper"],
        default="auto",
        help="Whisper backend for offline mode: 'auto' (detect Apple Silicon MLX), 'mlx', or 'faster-whisper' [default: auto]"
    )
    parser.add_argument(
        "--whisper-model",
        default="small",
        help="Whisper model size for offline mode (e.g. tiny, base, small, medium, large-v3) [default: small]"
    )
    parser.add_argument(
        "--no-diarization",
        action="store_true",
        help="Disable acoustic speaker diarization in offline Whisper mode"
    )
    parser.add_argument(
        "--clustering-threshold",
        type=float,
        default=0.68,
        help="Acoustic clustering threshold for Sherpa-ONNX [default: 0.68]"
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        default=-1,
        help="Exact number of speakers if known, otherwise -1 for automatic detection"
    )
    parser.add_argument(
        "--embedding-type",
        choices=["eres2net", "cam++"],
        default="eres2net",
        help="Sherpa-ONNX speaker embedding model architecture [default: eres2net]"
    )
    parser.add_argument(
        "--project",
        help="Google Cloud project ID for Vertex AI (default: GOOGLE_CLOUD_PROJECT/GCP_PROJECT env var, or the ADC default project)"
    )
    parser.add_argument(
        "--region",
        help="Google Cloud region for Vertex AI (default: GOOGLE_CLOUD_LOCATION env var, or global)"
    )
    parser.add_argument(
        "--bucket",
        help="GCS bucket name used to stage local audio/video for Gemini (default: MEETING_STORAGE_BUCKET env var). Not needed for YouTube URLs or --engine whisper."
    )
    parser.add_argument(
        "--transcribe-model",
        default=os.environ.get("TRANSCRIBE_MODEL") or "gemini-3.5-transcribe-preview",
        help="Gemini cloud transcription model (default: TRANSCRIBE_MODEL env var, or gemini-3.5-transcribe-preview)"
    )
    parser.add_argument(
        "--summary-model",
        default=os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash",
        help="Gemini executive summary and vision model (default: SUMMARY_MODEL env var, or gemini-3.8-flash)"
    )
    parser.add_argument(
        "--outline",
        help="Path to external meeting notice, outline, or agenda document (enhances entity extraction and terminology accuracy)"
    )
    parser.add_argument(
        "--force-glossary",
        action="store_true",
        help="Force re-extraction of global consistency glossary (bypassing cache)"
    )
    parser.add_argument(
        "--no-glossary",
        action="store_true",
        help="Skip global consistency glossary extraction"
    )
    parser.add_argument(
        "--no-player",
        action="store_true",
        help="Disable interactive HTML player generation"
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Do not compress audio before uploading to Gemini API (use original quality)"
    )
    parser.add_argument(
        "--summary-language",
        type=str,
        default=None,
        help="Target language for meeting summary and analysis (default: auto mirrors user prompt language; or specify en, zh-TW, ja, ko, etc.)"
    )
    parser.add_argument(
        "--only-transcript",
        action="store_true",
        help="Run only Stage 1 transcription and output verbatim transcript without Stage 2 summarization (ideal for Agent-native processing)"
    )
    parser.add_argument(
        "--language",
        type=str,
        default="auto",
        help="Spoken audio language code for offline Whisper ASR (e.g. 'auto', 'en', 'zh', 'ja') [default: auto]"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Launch lightweight local HTTP server and open browser for HTML player (recommended for YouTube embedded playback)"
    )


    args = parser.parse_args()

    try:
        generate_meeting_minutes_and_transcript(
            input_source=args.input_source,
            output_file=args.output,
            engine=args.engine,
            whisper_backend=args.whisper_backend,
            whisper_model=args.whisper_model,
            enable_diarization=not args.no_diarization,
            clustering_threshold=args.clustering_threshold,
            num_speakers=args.num_speakers,
            embedding_type=args.embedding_type,
            project_id=args.project,
            location=args.region,
            bucket_name=args.bucket,
            transcribe_model=args.transcribe_model,
            summary_model=args.summary_model,
            outline=args.outline,
            force_glossary=args.force_glossary,
            no_glossary=args.no_glossary,
            no_player=args.no_player,
            compress=not args.no_compress,
            summary_language=args.summary_language,
            only_transcript=args.only_transcript,
            language=args.language,
            agentic=args.agentic,
            extract_audio=args.extract_audio,
            serve=args.serve,
        )

    except Exception as e:
        print(f"\n[❌ Error] Execution failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
