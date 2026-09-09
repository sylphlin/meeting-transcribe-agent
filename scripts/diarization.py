"""
scripts/diarization.py - Local Acoustic Speaker Diarization & Whisper/MLX Transcription.
Integrates Sherpa-ONNX (3D-Speaker / PyAnnote), word-level alignment, and VAD audio processing.
"""

import os
import re
import sys
import time
import shutil
import urllib.request
import subprocess
import platform
from pathlib import Path
from typing import Tuple, List, Dict, Any

from scripts.audio_utils import get_audio_duration, format_offset, compress_audio_for_upload


def join_cjk_text(tokens: List[str]) -> str:
    """Intelligently concatenate tokens respecting CJK punctuation without extra spaces."""
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
        is_cjk = lambda c: '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef'
        if is_cjk(last_char) or is_cjk(first_char):
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
    print(f"[*] 執行音訊前處理增強 (高低通濾波 80Hz~7.5kHz + 動態響度正規化)...")
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
        print(f"[!] 音訊增強警告 ({e})，使用原始 WAV。")
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
        print("[*] 下載 Pyannote 語者切分模型...")
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
        print(f"[*] 下載聲紋特徵模型 `{embedding_type}`...")
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
    initial_prompt: str = ""
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
            print(f"[*] [本地聲學] 載入 Sherpa-ONNX 離線聲紋分離引擎 (特徵模型: {embedding_type}, 閥值: {clustering_threshold})...")
            
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
                sample_rate = wf.getframerate()
                num_frames = wf.getnframes()
                audio_bytes = wf.readframes(num_frames)
                import numpy as np
                samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            print("[*] [本地聲學] 執行聲紋分群與交疊語音偵測...")
            diar_res = diarizer.process(samples)
            for seg in diar_res:
                diar_segments.append(seg)
            print(f"[*] [本地聲學] 分離完成！辨識出 {len(diar_segments)} 個聲學語者片段。")

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
                print(f"[*] [本地聲學] 偵測到 {len(overlap_intervals)} 處重疊/交疊發言區間。")

        except Exception as e:
            print(f"[!] 聲學聲紋分離跳過或失敗 ({e})，使用純文字轉譯。")
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
    final_prompt = initial_prompt or "以下為會議錄音之逐字稿，包含台灣繁體中文對話與專業術語。"

    if use_mlx:
        import mlx_whisper
        mlx_repo = f"mlx-community/whisper-{model_size}-mlx"
        print(f"[*] [本地 ASR] 啟用 Apple Silicon Metal GPU 加速 (`mlx-whisper` {model_size})...")
        res = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=mlx_repo,
            language="zh",
            task="transcribe",
            word_timestamps=True,
            initial_prompt=final_prompt
        )
        for s in res.get("segments", []):
            whisper_segs.append({
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": s["text"].strip()
            })
            for w in s.get("words", []):
                words_list.append({
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                    "word": w["word"],
                    "probability": float(w.get("probability", 1.0))
                })
    else:
        from faster_whisper import WhisperModel
        print(f"[*] [本地 ASR] 載入 Whisper 模型 `{model_size}` (int8/CPU, word_timestamps=True)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=True,
            initial_prompt=final_prompt
        )
        for s in segments:
            whisper_segs.append({
                "start": s.start,
                "end": s.end,
                "text": s.text.strip()
            })
            if s.words:
                for w in s.words:
                    words_list.append({
                        "start": w.start,
                        "end": w.end,
                        "word": w.word,
                        "probability": w.probability
                    })

    # Step 3: Word-level Temporal Voting Alignment & Overlap Tagging
    if diar_segments and words_list:
        print("[*] 執行「單詞級 (Word-level)」細粒度時間戳記聲紋投票對齊與重疊語音融合...")
        for w in words_list:
            w_s, w_e = w["start"], w["end"]
            best_spk = None
            max_overlap = 0.0
            for d in diar_segments:
                overlap = max(0.0, min(w_e, d.end) - max(w_s, d.start))
                if overlap > max_overlap:
                    max_overlap = overlap
                    best_spk = str(d.speaker)
            
            # Micro-pause proximity match
            if best_spk is None or max_overlap <= 0.0:
                closest_dist = 999.0
                for d in diar_segments:
                    dist = min(abs(w_s - d.end), abs(w_e - d.start))
                    if dist < closest_dist and dist < 1.0:
                        closest_dist = dist
                        best_spk = str(d.speaker)

            # Check overlap tagging
            is_overlap = False
            for ov in overlap_intervals:
                if max(0.0, min(w_e, ov["end"]) - max(w_s, ov["start"])) > 0.2:
                    is_overlap = True
                    break

            w["speaker"] = (best_spk or "0") + ("_overlap" if is_overlap else "")

        # Group words into speaker turns
        aligned_turns = []
        cur_spk = None
        cur_words = []
        turn_start = None
        turn_end = None

        for w in words_list:
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
                        aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{clean_spk} (同時發言/重疊)**: {sentence}")
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
                    aligned_turns.append(f"[{s_fmt} - {e_fmt}] **spk_{clean_spk} (同時發言/重疊)**: {sentence}")
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
    print(f"[*] 本地處理完成！耗時: {asr_time:.1f}s，取得 {len(raw_text)} 字元。")
    return raw_text, asr_time
