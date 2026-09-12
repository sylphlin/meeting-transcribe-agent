"""
scripts/diarization.py - Local Acoustic Speaker Diarization & Whisper/MLX Transcription.
Integrates Sherpa-ONNX (3D-Speaker / PyAnnote), word-level alignment, and VAD audio processing.
"""

import time
import shutil
import urllib.request
import subprocess
import platform
from pathlib import Path
from typing import Tuple, List

from .audio_utils import format_offset


def join_cjk_text(tokens: List[str]) -> str:
    """Intelligently concatenate tokens respecting CJK punctuation without extra spaces."""
    def is_cjk_char(c: str) -> bool:
        if not c:
            return False
        cp = ord(c)
        return (
            (0x4E00 <= cp <= 0x9FFF) or   # CJK Unified Ideographs (Chinese / Kanji)
            (0x3400 <= cp <= 0x4DBF) or   # CJK Extension A
            (0x3040 <= cp <= 0x309F) or   # Japanese Hiragana
            (0x30A0 <= cp <= 0x30FF) or   # Japanese Katakana
            (0x31F0 <= cp <= 0x31FF) or   # Katakana Extensions
            (0xAC00 <= cp <= 0xD7AF) or   # Korean Hangul Syllables
            (0x1100 <= cp <= 0x11FF) or   # Korean Hangul Jamo
            (0x3130 <= cp <= 0x318F) or   # Korean Compatibility Jamo
            (0x3000 <= cp <= 0x303F) or   # CJK Symbols & Punctuation
            (0xFF00 <= cp <= 0xFFEF)      # Fullwidth / Halfwidth Forms
        )

    result = ""
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if not result:
            result = token
            continue
        last_char = result[-1]
        first_char = token[0]
        if is_cjk_char(last_char) or is_cjk_char(first_char):
            result += token
        else:
            result += " " + token
    return result



def preprocess_audio_for_diarization(audio_path: Path) -> Path:
    """Convert audio to 16kHz Mono WAV required by Sherpa-ONNX."""
    out_wav = audio_path.parent / f"temp_diar_{audio_path.stem}.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", "16000",
        str(out_wav)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return out_wav


def enhance_speech_audio(wav_path: Path) -> Path:
    """Denoise and speech-enhance WAV audio with highpass, lowpass, and EBU R128 loudness normalization."""
    enhanced_wav = wav_path.parent / f"temp_enhanced_{wav_path.stem}.wav"
    print(f"[*] Preprocessing audio enhancement (bandpass 80Hz~7.5kHz + dynamic loudness normalization)...")
    cmd = [
        "ffmpeg", "-y", "-i", str(wav_path),
        "-af", "highpass=f=80,lowpass=f=7500,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "16000", "-ac", "1",
        str(enhanced_wav)
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return enhanced_wav
    except Exception as e:
        print(f"[!] Audio enhancement warning ({e}), falling back to raw WAV.")
        return wav_path


def download_file_safe(url: str, dest_path: Path):
    """Download file with unverified SSL context fallback for corporate/local Mac environments."""
    import ssl
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as response, open(dest_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as response, open(dest_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)


def ensure_sherpa_models(embedding_type: str = "eres2net") -> Tuple[str, str]:
    """Ensure Sherpa-ONNX Pyannote segmentation and speaker embedding models are downloaded locally."""
    cache_dir = Path.home() / ".cache" / "sherpa_onnx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Pyannote Segmentation Model
    seg_dir = cache_dir / "sherpa-onnx-pyannote-segmentation-3-0"
    seg_model = seg_dir / "model.onnx"
    if not seg_model.exists():
        print("[*] Downloading Pyannote speaker segmentation model...")
        url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        tar_path = cache_dir / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        download_file_safe(url, tar_path)
        shutil.unpack_archive(str(tar_path), str(cache_dir))
        if tar_path.exists():
            tar_path.unlink()

    # 2. Embedding Model (CAM++ / ERes2Net)
    if embedding_type == "cam++":
        model_file = cache_dir / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
        url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
    else:  # default eres2net
        model_file = cache_dir / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

    if not model_file.exists():
        print(f"[*] Downloading speaker embedding model `{embedding_type}`...")
        download_file_safe(url, model_file)


    return str(seg_model), str(model_file)


def transcribe_with_local_whisper_and_diarization(
    audio_path: Path,
    model_size: str = "small",
    enable_diarization: bool = True,
    clustering_threshold: float = 0.68,
    num_speakers: int = -1,
    embedding_type: str = "eres2net",
    whisper_backend: str = "auto",
    initial_prompt: str = "",
    language: str | None = None
) -> Tuple[str, float]:
    """Transcribe audio with Local Whisper + Sherpa-ONNX Diarization + Word-level Alignment."""
    t_start = time.time()
    diar_segments = []
    overlap_intervals = []

    # Step 1: Local Diarization with Sherpa-ONNX
    if enable_diarization:
        wav_path = preprocess_audio_for_diarization(audio_path)
        enhanced_wav = enhance_speech_audio(wav_path)
        if enhanced_wav != wav_path and wav_path.exists():
            wav_path.unlink()

        try:
            import sherpa_onnx
            seg_model, emb_model = ensure_sherpa_models(embedding_type=embedding_type)
            print(f"[*] [Offline Acoustic] Loading Sherpa-ONNX diarization engine (embedding: {embedding_type}, threshold: {clustering_threshold})...")
            
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model))
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)),
                clustering=sherpa_onnx.FastClusteringConfig(
                    threshold=clustering_threshold,
                    num_clusters=num_speakers if num_speakers > 0 else -1
                )
            )
            diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
            
            import wave
            with wave.open(str(enhanced_wav), "rb") as wf:
                num_frames = wf.getnframes()
                audio_bytes = wf.readframes(num_frames)
                import numpy as np
                samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            print("[*] [Offline Acoustic] Running speaker clustering and overlap detection...")
            diar_res = diarizer.process(samples)
            for seg in diar_res.sort_by_start_time():
                diar_segments.append(seg)
            print(f"[*] [Offline Acoustic] Diarization complete: identified {len(diar_segments)} speaker segments.")

            # Check overlap intervals
            for i in range(len(diar_segments)):
                for j in range(i + 1, len(diar_segments)):
                    d1, d2 = diar_segments[i], diar_segments[j]
                    if d1.speaker != d2.speaker:
                        ov_s = max(d1.start, d2.start)
                        ov_e = min(d1.end, d2.end)
                        if ov_e - ov_s >= 0.5:
                            overlap_intervals.append({
                                "start": ov_s,
                                "end": ov_e,
                                "speakers": sorted([str(d1.speaker), str(d2.speaker)])
                            })
            if overlap_intervals:
                print(f"[*] [Offline Acoustic] Detected {len(overlap_intervals)} overlapping speech intervals.")

        except Exception as e:
            print(f"[!] Speaker diarization skipped or failed ({e}), proceeding with verbatim transcription.")
        finally:
            if enhanced_wav != audio_path and enhanced_wav.exists():
                enhanced_wav.unlink()

    # Step 2: Determine Whisper Backend & Transcribe
    use_mlx = False
    if whisper_backend == "mlx":
        use_mlx = True
    elif whisper_backend == "auto":
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            try:
                import mlx_whisper
                use_mlx = True
            except ImportError:
                use_mlx = False

    words_list = []
    whisper_segs = []
    final_prompt = initial_prompt or ""

    if use_mlx:
        import mlx_whisper
        mlx_repo = f"mlx-community/whisper-{model_size}-mlx"
        print(f"[*] [Local ASR] Enabling Apple Silicon Metal GPU acceleration (`mlx-whisper` {model_size})...")
        transcribe_kwargs = {
            "path_or_hf_repo": mlx_repo,
            "task": "transcribe",
            "word_timestamps": True,
        }
        if language and language.lower() != "auto":
            transcribe_kwargs["language"] = language
        if final_prompt:
            transcribe_kwargs["initial_prompt"] = final_prompt
        res = mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
        for s in res.get("segments", []):
            text = s["text"].strip()
            if text and text not in [".", "..", "...", ",", "，", "。"]:
                whisper_segs.append({
                    "start": float(s["start"]),
                    "end": float(s["end"]),
                    "text": text
                })
            for w in s.get("words", []):
                word_clean = w["word"].strip()
                if word_clean and word_clean not in [".", "..", "...", ",", "，", "。"]:
                    words_list.append({
                        "start": float(w["start"]),
                        "end": float(w["end"]),
                        "word": w["word"],
                        "probability": float(w.get("probability", 1.0))
                    })
    else:
        from faster_whisper import WhisperModel
        print(f"[*] [Local ASR] Loading Whisper model `{model_size}` (int8/CPU, word_timestamps=True)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        whisper_kwargs = {
            "beam_size": 5,
            "vad_filter": True,
            "vad_parameters": dict(min_silence_duration_ms=500),
            "word_timestamps": True,
        }
        if language and language.lower() != "auto":
            whisper_kwargs["language"] = language
        if final_prompt:
            whisper_kwargs["initial_prompt"] = final_prompt
        segments, info = model.transcribe(str(audio_path), **whisper_kwargs)
        for s in segments:
            text = s.text.strip()
            if text and text not in [".", "..", "...", ",", "，", "。"]:
                whisper_segs.append({
                    "start": s.start,
                    "end": s.end,
                    "text": text
                })
            if s.words:
                for w in s.words:
                    word_clean = w.word.strip()
                    if word_clean and word_clean not in [".", "..", "...", ",", "，", "。"]:
                        words_list.append({
                            "start": w.start,
                            "end": w.end,
                            "word": w.word,
                            "probability": w.probability
                        })


    # Step 3: Word-level Temporal Voting Alignment & Overlap Tagging (Optimized Sliding Window)
    if diar_segments and words_list:
        print("[*] Aligning word-level timestamps with speaker diarization clusters and merging overlaps...")
        valid_words = []
        d_idx = 0
        num_d = len(diar_segments)

        for w in words_list:
            w_s, w_e = w["start"], w["end"]

            # Fast-forward sliding window start pointer: skip segments ending well before w_s
            while d_idx < num_d and diar_segments[d_idx].end < (w_s - 1.5):
                d_idx += 1

            best_spk = None
            max_overlap = 0.0
            closest_dist = 999.0

            # Scan only segments in temporal vicinity of word [w_s - 1.5, w_e + 1.5]
            for j in range(d_idx, num_d):
                d = diar_segments[j]
                if d.start > (w_e + 1.5):
                    break

                overlap = max(0.0, min(w_e, d.end) - max(w_s, d.start))
                if overlap > max_overlap:
                    max_overlap = overlap
                    best_spk = str(d.speaker)

                # Micro-pause proximity match
                dist = min(abs(w_s - d.end), abs(w_e - d.start))
                if dist < closest_dist and dist < 1.0:
                    closest_dist = dist
                    if best_spk is None or max_overlap <= 0.0:
                        best_spk = str(d.speaker)

            # Drop acoustic silence hallucinations
            if best_spk is None:
                continue

            # Check overlap tagging in vicinity
            is_overlap = False
            for ov in overlap_intervals:
                if ov["end"] < (w_s - 0.5):
                    continue
                if ov["start"] > (w_e + 0.5):
                    break
                if max(0.0, min(w_e, ov["end"]) - max(w_s, ov["start"])) > 0.2:
                    is_overlap = True
                    break

            w["speaker"] = best_spk + ("_overlap" if is_overlap else "")
            valid_words.append(w)

        # Group words into speaker turns
        aligned_turns = []
        cur_spk = None
        cur_words = []
        turn_start = None
        turn_end = None

        for w in valid_words:

            spk = w["speaker"]
            if cur_spk is None:
                cur_spk = spk
                cur_words = [w["word"]]
                turn_start = w["start"]
                turn_end = w["end"]
            elif cur_spk == spk and (w["start"] - turn_end) <= 1.8:
                cur_words.append(w["word"])
                turn_end = w["end"]
            else:
                s_fmt = format_offset(turn_start)
                e_fmt = format_offset(turn_end)
                sentence = join_cjk_text(cur_words)
                if sentence:
                    if "_overlap" in cur_spk:
                        clean_spk = cur_spk.replace("_overlap", "")
                        aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{clean_spk} (Overlap)**: {sentence}")
                    else:
                        aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{cur_spk}**: {sentence}")
                
                cur_spk = spk
                cur_words = [w["word"]]
                turn_start = w["start"]
                turn_end = w["end"]

        if cur_words:
            s_fmt = format_offset(turn_start)
            e_fmt = format_offset(turn_end)
            sentence = join_cjk_text(cur_words)
            if sentence:
                if "_overlap" in cur_spk:
                    clean_spk = cur_spk.replace("_overlap", "")
                    aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{clean_spk} (Overlap)**: {sentence}")
                else:
                    aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{cur_spk}**: {sentence}")

        raw_text = "\n\n".join(aligned_turns)
    elif diar_segments:
        aligned_turns = []
        for s in whisper_segs:
            s_fmt = format_offset(s["start"])
            e_fmt = format_offset(s["end"])
            aligned_turns.append(f"[{s_fmt} - {e_fmt}] {s['text']}")
        raw_text = "\n".join(aligned_turns)
    else:
        lines = []
        for w in whisper_segs:
            text = w["text"].strip()
            if text:
                lines.append(f"[{format_offset(w['start'])} - {format_offset(w['end'])}] {text}")
        raw_text = "\n".join(lines)

    asr_time = time.time() - t_start
    print(f"[*] Local ASR complete in {asr_time:.1f}s ({len(raw_text)} characters).")
    return raw_text, asr_time


