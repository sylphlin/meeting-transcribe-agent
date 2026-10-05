#!/usr/bin/env python3
"""
Unit tests for Dual-Pass Chirp 3 Acoustic Diarization and Multilingual Alignment.

Covers:
1. Multilingual token normalization (English, Traditional Chinese, Japanese).
2. Degenerate n-gram repetition loop suppression.
3. Hybrid CJK character + Western word LCS timestamp projection and interpolation.
4. Overlap midpoint deduplication across Track B chunk boundaries.
5. Turn aggregation and semantic paragraph re-projection with word timestamps.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "meeting-transcribe-agent"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.alignment_engine import (
    AlignmentEngine,
    deduplicate_overlap_micro_words,
    join_multilingual_words,
    normalize_multilingual_token,
    normalize_speaker_label,
)
from scripts.chirp3_engine import (
    compute_track_b_chunk_windows,
    resolve_chirp3_language_codes,
    validate_chirp_model,
)
from scripts.gemini_engine import refine_verbatim_transcript_chunks
from scripts.audio_utils import detect_audio_speech_onset, format_offset

import shutil
import subprocess
import tempfile


class TestTimestampFormattingAndOnset(unittest.TestCase):
    """Test conservative timestamp formatting and FFmpeg physical speech onset detection."""

    def test_format_offset_floor_ceil(self):
        self.assertEqual(format_offset(940.96, mode="floor"), "15:40")
        self.assertEqual(format_offset(937.26, mode="ceil"), "15:38")
        self.assertEqual(format_offset(937.0, mode="ceil"), "15:37")
        self.assertEqual(format_offset(940.96), "15:41")
        self.assertEqual(format_offset(3723.4, mode="floor"), "01:02:03")
        self.assertEqual(format_offset(None), "00:00")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_detect_audio_speech_onset_leading_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "probe.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                    "-filter_complex", "[0:a]atrim=0:6[a];[1:a]atrim=0:3[b];[a][b]concat=n=2:v=0:a=1",
                    "-ac", "1", "-ar", "16000", str(wav),
                ],
                check=True,
            )
            onset = detect_audio_speech_onset(wav)
            self.assertGreaterEqual(onset, 5.5)
            self.assertLessEqual(onset, 6.5)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_detect_audio_speech_onset_immediate_speech_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "probe.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=4",
                    "-ac", "1", "-ar", "16000", str(wav),
                ],
                check=True,
            )
            self.assertEqual(detect_audio_speech_onset(wav), 0.0)


class TestMultilingualNormalization(unittest.TestCase):
    """Test multilingual token and speaker normalization."""

    def test_validate_chirp_model(self):
        self.assertEqual(validate_chirp_model("chirp_3"), "chirp_3")
        self.assertEqual(validate_chirp_model("chirp_4"), "chirp_4")
        with self.assertRaises(ValueError):
            validate_chirp_model("invalid_non_chirp_model")

    def test_resolve_chirp3_language_codes(self):
        self.assertEqual(resolve_chirp3_language_codes("auto", for_diarization=True), ["auto"])
        self.assertEqual(resolve_chirp3_language_codes("auto", for_diarization=False), ["auto"])
        self.assertEqual(
            resolve_chirp3_language_codes("cmn-Hant-TW", for_diarization=True),
            ["cmn-Hans-CN", "en-US"],
        )
        self.assertEqual(
            resolve_chirp3_language_codes("cmn-Hant-TW", for_diarization=False),
            ["cmn-Hant-TW", "en-US"],
        )
        self.assertEqual(resolve_chirp3_language_codes("en-US", for_diarization=True), ["en-US"])

    def test_normalize_multilingual_token(self):
        self.assertEqual(normalize_multilingual_token("Hello,"), "hello")
        self.assertEqual(normalize_multilingual_token("專案「A」"), "專案a")
        self.assertEqual(normalize_multilingual_token("会議です。"), "会議です")
        self.assertEqual(normalize_multilingual_token("2026-Q3!"), "2026q3")
        self.assertEqual(normalize_multilingual_token("..."), "")

    def test_normalize_speaker_label(self):
        self.assertEqual(normalize_speaker_label(1), "Speaker 1")
        self.assertEqual(normalize_speaker_label("spk_2"), "Speaker 2")
        self.assertEqual(normalize_speaker_label("Speaker 3"), "Speaker 3")
        self.assertEqual(normalize_speaker_label(""), "Speaker 1")

    def test_join_multilingual_words(self):
        words = ["Hello", "world,", "這是", "測試", "project", "A."]
        self.assertEqual(join_multilingual_words(words), "Hello world, 這是測試 project A.")


class TestRepetitionLoopSuppression(unittest.TestCase):
    """Test suppression of ASR n-gram repetition loops."""

    def test_suppress_five_gram_loop(self):
        engine = AlignmentEngine()
        base_words = [
            {"word": "We", "speaker": "Speaker 1"},
            {"word": "will", "speaker": "Speaker 1"},
            {"word": "review", "speaker": "Speaker 1"},
        ]
        loop_phrase = ["I", "don't", "know", "how", "to"]
        loop_words = [
            {"word": w, "speaker": "Speaker 1"}
            for _ in range(6)
            for w in loop_phrase
        ]
        tail_words = [
            {"word": "proceed", "speaker": "Speaker 1"},
            {"word": "today.", "speaker": "Speaker 1"},
        ]
        cleaned = engine.suppress_repetition_loops(base_words + loop_words + tail_words)
        text = join_multilingual_words([w["word"] for w in cleaned])
        self.assertEqual(text.count("I don't know how to"), 1)
        self.assertTrue(text.startswith("We will review"))
        self.assertTrue(text.endswith("proceed today."))


class TestHybridMultilingualAlignment(unittest.TestCase):
    """Test Track B physical-timeline master alignment with Track A speaker labels."""

    def test_cjk_different_segmentation_alignment(self):
        """Verify Track B words keep physical timestamps and inherit Track A speakers across different CJK segmentation."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "各位好，", "speaker": "Speaker 1"},
            {"word": "今天我們", "speaker": "Speaker 1"},
            {"word": "討論", "speaker": "Speaker 1"},
            {"word": "雲端架構。", "speaker": "Speaker 1"},
            {"word": "謝謝大家。", "speaker": "Speaker 2"},
        ]
        micro_words = [
            {"word": "各位", "start": 0.0, "end": 0.5},
            {"word": "好", "start": 0.5, "end": 0.8},
            {"word": "今天", "start": 1.0, "end": 1.4},
            {"word": "我們討論", "start": 1.4, "end": 2.2},
            {"word": "雲端", "start": 2.2, "end": 2.6},
            {"word": "架構", "start": 2.6, "end": 3.0},
            {"word": "謝謝", "start": 3.5, "end": 4.0},
            {"word": "大家", "start": 4.0, "end": 4.5},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words)
        # Output is the Track B word stream (8 physical words), every word timed
        self.assertEqual(len(aligned), 8)
        for item in aligned:
            self.assertIsNotNone(item["start"])
            self.assertIsNotNone(item["end"])
        self.assertEqual([w["speakerLabel"] for w in aligned[:6]], ["Speaker 1"] * 6)
        self.assertEqual([w["speakerLabel"] for w in aligned[6:]], ["Speaker 2"] * 2)
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 2)
        self.assertAlmostEqual(turns[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(turns[0]["end"], 3.0, places=2)
        self.assertEqual(turns[1]["speaker"], "Speaker 2")
        self.assertAlmostEqual(turns[1]["start"], 3.5, places=2)

    def test_cross_script_simplified_track_a_to_traditional_track_b_alignment(self):
        """Verify Track A (cmn-Hans-CN diarization) aligns with Track B (cmn-Hant-TW) and output text is native Traditional Chinese."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "各位好,", "speaker": "Speaker 1"},
            {"word": "今天我们", "speaker": "Speaker 1"},
            {"word": "讨论", "speaker": "Speaker 1"},
            {"word": "云端架构与会议记录。", "speaker": "Speaker 1"},
        ]
        micro_words = [
            {"word": "各位", "start": 0.0, "end": 0.36},
            {"word": "好,", "start": 0.36, "end": 0.84},
            {"word": "今天", "start": 0.96, "end": 1.44},
            {"word": "我們", "start": 1.44, "end": 1.70},
            {"word": "討論", "start": 1.70, "end": 2.00},
            {"word": "雲端", "start": 2.00, "end": 2.30},
            {"word": "架構與", "start": 2.30, "end": 2.75},
            {"word": "會議紀錄。", "start": 2.75, "end": 3.40},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words, prefer_micro_cjk=True)
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "各位好, 今天我們討論雲端架構與會議紀錄。")
        self.assertAlmostEqual(turns[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(turns[0]["end"], 3.40, places=2)

    def test_short_track_a_only_block_is_dropped_without_interpolation(self):
        """Verify short Track A-only words (no Track B acoustic evidence) are dropped instead of interpolated."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "Start", "speaker": "Speaker 1"},
            {"word": "middle", "speaker": "Speaker 1"},
            {"word": "tokens", "speaker": "Speaker 1"},
            {"word": "end.", "speaker": "Speaker 1"},
        ]
        micro_words = [
            {"word": "Start", "start": 10.0, "end": 11.0},
            {"word": "end.", "start": 14.0, "end": 15.0},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words)
        self.assertEqual([w["word"] for w in aligned], ["Start", "end."])
        self.assertAlmostEqual(aligned[0]["start"], 10.0, places=2)
        self.assertAlmostEqual(aligned[1]["end"], 15.0, places=2)

    def test_long_track_a_only_block_bounded_interpolation(self):
        """Verify a long Track A-only block (>= 8 units) is kept and interpolated strictly between neighbor anchors."""
        engine = AlignmentEngine()
        macro_words = [{"word": "alpha", "speaker": "Speaker 1"}]
        macro_words += [{"word": f"w{i}", "speaker": "Speaker 1"} for i in range(10)]
        macro_words += [{"word": "omega", "speaker": "Speaker 1"}]
        micro_words = [
            {"word": "alpha", "start": 100.0, "end": 100.5},
            {"word": "omega", "start": 110.0, "end": 110.5},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words)
        self.assertEqual(len(aligned), 12)
        for w in aligned:
            self.assertIsNotNone(w["start"])
        for i in range(len(aligned) - 1):
            self.assertLessEqual(aligned[i]["end"], aligned[i + 1]["start"] + 1e-6)
        self.assertGreaterEqual(aligned[1]["start"], 100.5)
        self.assertLessEqual(aligned[10]["end"], 110.0)

    def test_short_opening_sentence_simplified_to_traditional_at_late_onset(self):
        """TC-01 & TC-03: Verify a short Simplified Track A opening after 7m35s silence locks to Track B at 455.0s."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "请问各位同仁", "speaker": "Speaker 1"},
            {"word": "对于上次会议纪录", "speaker": "Speaker 1"},
            {"word": "有没有疑问？", "speaker": "Speaker 1"},
            {"word": "有没有要提出来的？", "speaker": "Speaker 1"},
        ]
        micro_words = [
            {"word": "請問", "start": 455.0, "end": 455.5},
            {"word": "各位同仁", "start": 455.5, "end": 456.4},
            {"word": "對於", "start": 456.4, "end": 456.9},
            {"word": "上次", "start": 456.9, "end": 457.4},
            {"word": "會議紀錄", "start": 457.4, "end": 458.5},
            {"word": "有沒有", "start": 458.5, "end": 459.1},
            {"word": "疑問？", "start": 459.1, "end": 459.8},
            {"word": "有沒有要", "start": 460.0, "end": 460.8},
            {"word": "提出來的？", "start": 460.8, "end": 461.8},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words, prefer_micro_cjk=True)
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "請問各位同仁對於上次會議紀錄有沒有疑問？有沒有要提出來的？")
        self.assertAlmostEqual(turns[0]["start"], 455.0, places=2)
        self.assertAlmostEqual(turns[0]["end"], 461.8, places=2)

    def test_leading_silence_noise_token_filtered_by_physical_onset(self):
        """TC-01: Verify a Track B noise token at 163s before the physical onset (453s) is discarded; first turn starts >= 453s."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "嗯", "speaker": "Speaker 1"},
            {"word": "請問各位同仁", "speaker": "Speaker 1"},
            {"word": "有沒有疑問", "speaker": "Speaker 1"},
        ]
        micro_words = [
            {"word": "嗯", "start": 163.0, "end": 163.4},
            {"word": "請問", "start": 455.0, "end": 455.5},
            {"word": "各位同仁", "start": 455.5, "end": 456.4},
            {"word": "有沒有疑問", "start": 456.5, "end": 457.6},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words, physical_onset_sec=453.15)
        self.assertEqual([w["word"] for w in aligned], ["請問", "各位同仁", "有沒有疑問"])
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 1)
        self.assertGreaterEqual(turns[0]["start"], 453.15)
        self.assertAlmostEqual(turns[0]["start"], 455.0, places=2)

    def test_first_word_absorbing_leading_silence_is_clamped(self):
        """Verify a first Track B word with start=0 and end=4.24 (absorbed leading silence) is clamped to the physical onset."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "請", "speaker": "Speaker 1"},
            {"word": "問", "speaker": "Speaker 1"},
        ]
        micro_words = [
            {"word": "請", "start": 0.0, "end": 4.24},
            {"word": "問", "start": 4.24, "end": 4.44},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words, physical_onset_sec=4.0)
        self.assertAlmostEqual(aligned[0]["start"], 4.0, places=2)
        self.assertAlmostEqual(aligned[0]["end"], 4.24, places=2)

    def test_speaker_boundary_insert_words_follow_acoustic_gap(self):
        """Case 2: Track B-only filler words after a 3.7s pause follow the next speaker; previous turn keeps its physical end."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "時間", "speaker": "Speaker 1"},
            {"word": "5分鐘。", "speaker": "Speaker 1"},
            {"word": "我們市長", "speaker": "Speaker 2"},
            {"word": "還有各位長官", "speaker": "Speaker 2"},
        ]
        micro_words = [
            {"word": "時間", "start": 936.0, "end": 936.5},
            {"word": "五分鐘。", "start": 936.5, "end": 937.26},
            {"word": "那個", "start": 940.96, "end": 941.3},
            {"word": "呃", "start": 941.3, "end": 941.5},
            {"word": "我們市長", "start": 941.6, "end": 942.4},
            {"word": "還有各位長官", "start": 942.4, "end": 943.6},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words)
        spk_by_word = {w["word"]: w["speakerLabel"] for w in aligned}
        self.assertEqual(spk_by_word["五分鐘。"], "Speaker 1")
        self.assertEqual(spk_by_word["那個"], "Speaker 2")
        self.assertEqual(spk_by_word["呃"], "Speaker 2")
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 2)
        self.assertAlmostEqual(turns[0]["end"], 937.26, places=2)
        self.assertAlmostEqual(turns[1]["start"], 940.96, places=2)

    def test_trailing_unmatched_words_stay_with_previous_speaker(self):
        """Case 2: Track B-only tail words right after the previous speaker (small gap) stay with that speaker."""
        engine = AlignmentEngine()
        macro_words = [
            {"word": "時間", "speaker": "Speaker 1"},
            {"word": "我們市長", "speaker": "Speaker 2"},
        ]
        micro_words = [
            {"word": "時間", "start": 936.0, "end": 936.5},
            {"word": "五分鐘。", "start": 936.5, "end": 937.26},
            {"word": "我們市長", "start": 940.96, "end": 941.8},
        ]
        aligned = engine.project_timestamps(macro_words, micro_words)
        spk_by_word = {w["word"]: w["speakerLabel"] for w in aligned}
        self.assertEqual(spk_by_word["五分鐘。"], "Speaker 1")
        turns = engine.aggregate_turns(aligned)
        self.assertEqual(len(turns), 2)
        self.assertAlmostEqual(turns[0]["end"], 937.26, places=2)
        self.assertAlmostEqual(turns[1]["start"], 940.96, places=2)


class TestOverlapDeduplication(unittest.TestCase):
    """Test Track B chunk window generation and 5-second overlap midpoint deduplication."""

    def test_compute_track_b_chunk_windows(self):
        windows = compute_track_b_chunk_windows(2500.0, chunk_duration_sec=1080.0, overlap_sec=5.0)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (0.0, 1080.0))
        self.assertEqual(windows[1], (1075.0, 2160.0))
        self.assertEqual(windows[2], (2155.0, 2500.0))

    def test_deduplicate_overlap_micro_words_monotonic(self):
        # Chunk 0 spans [0.0, 1080.0]; Chunk 1 spans [1075.0, 2160.0].
        # Overlap window is [1075.0, 1080.0], midpoint is 1077.5s.
        chunk_0_words = [
            {"word": "alpha", "start": 1074.0, "end": 1076.0},
            {"word": "beta", "start": 1076.0, "end": 1077.0},
            {"word": "gamma_duplicate", "start": 1078.0, "end": 1079.5},
        ]
        # Local offsets inside Chunk 1 (relative to c_start=1075.0)
        chunk_1_words = [
            {"word": "beta_duplicate", "start": 1.0, "end": 2.0},  # global 1076.0 - 1077.0 (< 1077.5)
            {"word": "gamma", "start": 3.0, "end": 4.5},           # global 1078.0 - 1079.5 (>= 1077.5)
            {"word": "delta", "start": 6.0, "end": 7.0},           # global 1081.0 - 1082.0
        ]
        merged = deduplicate_overlap_micro_words(
            [(0.0, 1080.0, chunk_0_words), (1075.0, 2160.0, chunk_1_words)]
        )
        words = [w["word"] for w in merged]
        self.assertEqual(words, ["alpha", "beta", "gamma", "delta"])
        for i in range(len(merged) - 1):
            self.assertLessEqual(merged[i]["start"], merged[i + 1]["start"])


class TestSemanticParagraphReprojection(unittest.TestCase):
    """Test LLM <PARA> semantic paragraph splitting and word-level timestamp re-projection."""

    def test_reproject_semantic_paragraphs_with_word_items(self):
        engine = AlignmentEngine()
        word_items = [
            {"word": "First", "start": 0.0, "end": 1.0, "speaker": "Speaker 1"},
            {"word": "topic", "start": 1.0, "end": 2.0, "speaker": "Speaker 1"},
            {"word": "discussion.", "start": 2.0, "end": 3.5, "speaker": "Speaker 1"},
            {"word": "Second", "start": 4.0, "end": 5.0, "speaker": "Speaker 1"},
            {"word": "topic", "start": 5.0, "end": 6.0, "speaker": "Speaker 1"},
            {"word": "starts", "start": 6.0, "end": 7.0, "speaker": "Speaker 1"},
            {"word": "now.", "start": 7.0, "end": 8.5, "speaker": "Speaker 1"},
        ]
        paragraphs = [
            "First topic discussion.",
            "Second topic starts now.",
        ]
        projected = engine.reproject_semantic_paragraphs(
            turn_words=word_items,
            paragraphs=paragraphs,
            fallback_start=0.0,
            fallback_end=8.5,
        )
        self.assertEqual(len(projected), 2)
        self.assertEqual(projected[0]["text"], "First topic discussion.")
        self.assertAlmostEqual(projected[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(projected[0]["end"], 3.5, places=2)
        self.assertEqual(projected[1]["text"], "Second topic starts now.")
        self.assertAlmostEqual(projected[1]["start"], 4.0, places=2)
        self.assertAlmostEqual(projected[1]["end"], 8.5, places=2)

    def test_refine_verbatim_transcript_chunks_expands_para_tags(self):
        raw_transcript = (
            "[00:00 - 01:20] **Speaker 1**: First quarterly revenue increased by 15 percent. "
            "Now let us review the infrastructure migration timeline for Q3."
        )
        fake_output = (
            "[00:00 - 01:20] **Speaker 1**: First quarterly revenue increased by 15 percent. "
            "<PARA> Now let us review the infrastructure migration timeline for Q3."
        )

        refined = refine_verbatim_transcript_chunks(
            raw_transcript_text=raw_transcript,
            sections_1_5="## 1. Metadata",
            global_glossary="",
            target_lang="English",
            call_track_fn=lambda contents, op_name: fake_output,
        )
        lines = [ln for ln in refined.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertNotIn("<PARA>", refined)
        self.assertTrue(lines[0].startswith("[00:00 - "))
        self.assertIn("First quarterly revenue increased by 15 percent.", lines[0])
        self.assertIn("Now let us review the infrastructure migration timeline for Q3.", lines[1])


if __name__ == "__main__":
    unittest.main()
