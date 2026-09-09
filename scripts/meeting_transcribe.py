#!/usr/bin/env python3
"""
scripts/meeting_transcribe.py - Main Pipeline Orchestrator for Meeting Transcribe Agent.
Universal Cloud-Scale Intelligence (gemini-3.5-transcribe) with Offline Whisper Backup.
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from scripts.audio_utils import (
    compress_audio_for_upload,
    get_audio_duration,
    format_offset,
)
from scripts.diarization import transcribe_with_local_whisper_and_diarization
from scripts.gemini_engine import (
    get_gemini_client,
    transcribe_with_gemini_cloud,
    generate_minutes_with_gemini,
)
from scripts.glossary import (
    extract_global_consistency_glossary,
    extract_keywords_from_glossary,
)
from scripts.canonicalizer import consolidate_meeting_minutes
from scripts.html_generator import generate_interactive_html


def generate_meeting_minutes_and_transcript(
    audio_file: str,
    output_file: str = None,
    engine: str = "gemini",
    whisper_backend: str = "auto",
    whisper_model: str = "small",
    enable_diarization: bool = True,
    clustering_threshold: float = 0.68,
    num_speakers: int = -1,
    embedding_type: str = "eres2net",
    api_key: str = None,
    transcribe_model: str = "gemini-3.5-transcribe",
    summary_model: str = "gemini-3.7-flash",
    outline: str = None,
    force_glossary: bool = False,
    no_glossary: bool = False,
    no_player: bool = False,
    compress: bool = True,
    summary_language: str = None,
    only_transcript: bool = False,
    language: str | None = "auto"
) -> Path:

    """
    End-to-End Meeting Transcription & Intelligence Pipeline:
    - Primary Engine: Cloud Gemini 3.5 Transcribe with ephemeral Files API auto-cleanup.
    - Fallback Engine: Offline Whisper (MLX Metal GPU / faster-whisper) + Sherpa-ONNX Diarization.
    1. Dual-Track Glossary Mining (Lightweight Audio Pre-scan + External Outline).
    2. Speech Recognition & Acoustic Diarization.
    3. Executive Minutes Structuring & Speaker Role Arbitration (Gemini 3.7 Flash).
    4. Canonical Speaker Identity Consolidation & Sequential Turn Merging.
    5. Interactive Zero-Dependency HTML Playback Player Generation.
    """
    audio_path = Path(audio_file).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    print(f"\n========================================================")
    print(f"🎙️  Meeting Transcribe Agent: {audio_path.name}")
    print(f"⚙️  Primary Engine: {engine.upper()} | Summary Model: {summary_model}")
    if engine.lower() == "whisper":
        print(f"   [Offline Setup] Backend: {whisper_backend} | Model: {whisper_model} | Diarization: {enable_diarization}")
    else:
        print(f"   [Cloud Setup] Model: {transcribe_model} (Zero-Persistence Files API)")
    print(f"========================================================\n")

    t_total_start = time.time()

    # Step 0: Initialize Gemini Client (optional if running pure offline whisper)
    client = None
    try:
        client = get_gemini_client(api_key=api_key)
    except Exception as e:
        if engine.lower() == "gemini":
            raise e
        print(f"[*] Note: Gemini API not available ({e}). Operating in pure offline mode.")

    # Step 1.5: Dual-Track Global Consistency Glossary
    global_glossary = ""
    glossary_keywords = ""
    if not no_glossary and client is not None:
        print("--- [Track 1 & 2] Global Terminology & Entity Mining ---")
        try:
            global_glossary, _ = extract_global_consistency_glossary(
                client=client,
                audio_path=audio_path,
                outline_path=outline,
                model=summary_model,
                force=force_glossary,
                compress_fn=compress_audio_for_upload if compress else None
            )
            if global_glossary:
                glossary_keywords = extract_keywords_from_glossary(global_glossary)
        except Exception as e:
            print(f"[!] Warning: Global glossary extraction failed ({e}), proceeding with standard pipeline.")

    # Step 1: Speech-to-Text Transcription & Acoustic Diarization
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
        print(f"--- [Stage 1/2] Cloud Multimodal Transcription ({transcribe_model}) ---")
        raw_transcript_text, asr_time = transcribe_with_gemini_cloud(
            client=client,
            audio_path=audio_path,
            model_name=transcribe_model,
            compress=compress
        )

    # Step 1.5: If only verbatim transcript is requested (for Agent-Native Stage 2 processing)
    if only_transcript:
        duration_sec = get_audio_duration(audio_path)
        dur_str = format_offset(duration_sec)
        eng_label = f"Local Whisper ({whisper_backend}/{whisper_model})" if engine.lower() == "whisper" else transcribe_model
        out_path = Path(output_file) if output_file else audio_path.parent / f"{audio_path.stem}_transcript.md"
        content = (
            f"# Meeting Transcript: {audio_path.stem}\n\n"
            f"- **Audio File**: `{audio_path.name}`\n"
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

    # Step 2: Meeting Minutes Structuring & Speaker Role Arbitration
    summary_time = 0.0
    final_markdown = ""

    if client is not None:
        print(f"\n--- [Stage 2/2] Meeting Minutes Structuring & Speaker Role Arbitration ({summary_model}) ---")
        try:
            final_markdown, summary_time = generate_minutes_with_gemini(
                client=client,
                audio_path=audio_path,
                raw_transcript_text=raw_transcript_text,
                global_glossary=global_glossary,
                summary_model=summary_model,
                summary_language=summary_language
            )
            # Step 2.5: Canonical Speaker ID Consolidation & Turn Merging
            print(f"[*] Consolidating canonical speaker identities and sequential turns...")
            final_markdown = consolidate_meeting_minutes(final_markdown)
        except Exception as e:
            print(f"[!] Warning: Gemini minutes structuring unavailable ({e}). Falling back to standalone verbatim transcript.")
            final_markdown = ""

    if not final_markdown:
        # Standalone verbatim report when offline or Gemini API unavailable
        duration_sec = get_audio_duration(audio_path)
        dur_str = format_offset(duration_sec)
        eng_label = f"Local Whisper ({whisper_backend}/{whisper_model})" if engine.lower() == "whisper" else transcribe_model
        final_markdown = (
            f"# Meeting Minutes & Transcript: {audio_path.stem}\n\n"
            f"- **Audio File**: `{audio_path.name}`\n"
            f"- **Duration**: {dur_str}\n"
            f"- **Transcription Engine**: {eng_label}\n"
            f"- **Generated At**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 🎙️ Verbatim Transcript\n\n"
            f"{raw_transcript_text.strip()}\n"
        )
        final_markdown = consolidate_meeting_minutes(final_markdown)

    total_time = time.time() - t_total_start

    # Save Markdown Output
    if output_file:
        out_path = Path(output_file)
    else:
        out_path = audio_path.parent / f"{audio_path.stem}_minutes.md"

    out_path.write_text(final_markdown, encoding="utf-8")
    print(f"\n========================================================")
    print(f"✅ Meeting transcript/minutes successfully generated!")
    print(f"📄 Output file: {out_path.resolve()}")
    print(f"⏱️  ASR Time: {asr_time:.1f}s | Summary Time: {summary_time:.1f}s | Total Time: {total_time:.1f}s")
    print(f"========================================================\n")

    # Step 3: Interactive HTML Player Generation (Skipped in only_transcript mode)
    if not no_player and not only_transcript:
        player_path = out_path.parent / f"{audio_path.stem}_player.html"
        try:
            generate_interactive_html(
                audio_file_path=audio_path,
                markdown_content=final_markdown,
                output_html_path=player_path
            )
        except Exception as e:
            print(f"[!] Warning: Interactive HTML player generation failed ({e}).")

    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Meeting Transcribe Agent - Universal Cloud-Scale Intelligence & Offline Whisper Backup Suite"
    )
    parser.add_argument("audio_file", help="Path to audio file (supports mp3, m4a, wav, mp4, aac, flac, etc.)")
    parser.add_argument("-o", "--output", help="Path to output Markdown file (default: <filename>_minutes.md)")

    parser.add_argument(
        "--engine",
        choices=["gemini", "whisper"],
        default="gemini",
        help="Transcription engine: 'gemini' (default, cloud Gemini 3.5 Transcribe) or 'whisper' (local offline backup)"
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
        choices=["eres2net", "pyannote", "cam++"],
        default="eres2net",
        help="Sherpa-ONNX speaker embedding model architecture [default: eres2net]"
    )
    parser.add_argument("--api-key", help="Gemini API key (default: reads GEMINI_API_KEY from environment or ~/.gemini/.env)")
    parser.add_argument(
        "--transcribe-model",
        default="gemini-3.5-transcribe",
        help="Gemini cloud transcription model [default: gemini-3.5-transcribe]"
    )
    parser.add_argument(
        "--summary-model",
        default="gemini-3.7-flash",
        help="Gemini executive summary and minutes model [default: gemini-3.7-flash]"
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


    args = parser.parse_args()

    try:
        generate_meeting_minutes_and_transcript(
            audio_file=args.audio_file,
            output_file=args.output,
            engine=args.engine,
            whisper_backend=args.whisper_backend,
            whisper_model=args.whisper_model,
            enable_diarization=not args.no_diarization,
            clustering_threshold=args.clustering_threshold,
            num_speakers=args.num_speakers,
            embedding_type=args.embedding_type,
            api_key=args.api_key,
            transcribe_model=args.transcribe_model,
            summary_model=args.summary_model,
            outline=args.outline,
            force_glossary=args.force_glossary,
            no_glossary=args.no_glossary,
            no_player=args.no_player,
            compress=not args.no_compress,
            summary_language=args.summary_language,
            only_transcript=args.only_transcript,
            language=args.language
        )

    except Exception as e:
        print(f"\n[❌ Error] Execution failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
