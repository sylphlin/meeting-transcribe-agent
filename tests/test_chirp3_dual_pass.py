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
from scripts.chirp3_engine import compute_track_b_chunk_windows, validate_chirp_model
from scripts.gemini_engine import refine_verbatim_transcript_chunks


class TestMultilingualNormalization(unittest.TestCase):
    """Test multilingual token and speaker normalization."""

    def test_validate_chirp_model(self):
        self.assertEqual(validate_chirp_model("chirp_3"), "chirp_3")
        self.assertEqual(validate_chirp_model("chirp_4"), "chirp_4")
        with self.assertRaises(ValueError):
            validate_chirp_model("invalid_non_chirp_model")

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
    """Test Track A and Track B hybrid LCS alignment across English and CJK."""

    def test_cjk_different_segmentation_alignment(self):
        """Verify 100% timestamp lock even when Track A and Track B segment CJK differently."""
        engine = AlignmentEngine()
        # Track A groups characters into compound words
        macro_words = [
            {"word": "各位好，", "speaker": "Speaker 1"},
            {"word": "今天我們", "speaker": "Speaker 1"},
            {"word": "討論", "speaker": "Speaker 1"},
            {"word": "雲端架構。", "speaker": "Speaker 1"},
            {"word": "謝謝大家。", "speaker": "Speaker 2"},
        ]
        # Track B emits individual characters or different pairs with physical timestamps
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
        self.assertEqual(len(aligned), 5)
        for item in aligned:
            self.assertIsNotNone(item["start"])
            self.assertIsNotNone(item["end"])
        self.assertAlmostEqual(aligned[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(aligned[3]["end"], 3.0, places=2)
        self.assertEqual(aligned[4]["speaker"], "Speaker 2")
        self.assertAlmostEqual(aligned[4]["start"], 3.5, places=2)

    def test_missing_micro_words_interpolation(self):
        """Verify linear interpolation when Track B misses middle tokens."""
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
        self.assertEqual(len(aligned), 4)
        for i in range(len(aligned) - 1):
            self.assertLessEqual(aligned[i]["start"], aligned[i + 1]["start"])
        self.assertAlmostEqual(aligned[0]["start"], 10.0, places=2)
        self.assertAlmostEqual(aligned[3]["end"], 15.0, places=2)


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
