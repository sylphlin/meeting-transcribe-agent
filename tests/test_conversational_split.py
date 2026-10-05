"""
tests/test_conversational_split.py - Stage 2 `<SPEAKER_SPLIT: Speaker X>` protocol.

Covers:
1. A line that mixes two speakers is split at the marker; both segments get physical timestamps
   from Track B word items; the head segment merges into the previous same-speaker turn.
2. A whole-line relabel (marker at the start of the text).
3. A marker that names an unknown speaker is dropped and the line is left intact.
4. `<SPEAKER_SPLIT>` and `<PARA>` on the same line.
5. Word cache keys use floor/ceil so `<PARA>` reprojection finds the physical words.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "meeting-transcribe-agent"))

from scripts import chirp3_engine  # noqa: E402
from scripts.gemini_engine import refine_verbatim_transcript_chunks  # noqa: E402


def _words(spec):
    """Build word items from (word, start, end) tuples."""
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


class TestConversationalSplit(unittest.TestCase):
    def setUp(self):
        chirp3_engine._LAST_ALIGNED_TURNS_CACHE.clear()

    def tearDown(self):
        chirp3_engine._LAST_ALIGNED_TURNS_CACHE.clear()

    def _seed_cache(self, turns):
        chirp3_engine._LAST_ALIGNED_TURNS_CACHE.extend(turns)

    def _run(self, raw, fake_output):
        refined = refine_verbatim_transcript_chunks(
            raw_transcript_text=raw,
            sections_1_5="## 1. Metadata",
            global_glossary="",
            target_lang="Traditional Chinese",
            call_track_fn=lambda contents, op_name: fake_output,
        )
        return [ln for ln in refined.splitlines() if ln.strip()]

    def test_mid_line_split_merges_head_into_previous_turn(self):
        # Turn A (Speaker 4) ends at 1558.2 -> "25:59" (ceil).
        # Turn B is mislabeled Speaker 1 and holds Speaker 4 words until 1561.64, then Speaker 1.
        self._seed_cache([
            {"start": 1541.0, "end": 1558.2, "word_items": _words([("五百零八倍", 1555.6, 1558.2)])},
            {"start": 1558.2, "end": 1563.92, "word_items": _words([
                ("是", 1558.2, 1558.4), ("它", 1558.4, 1558.6), ("的", 1558.6, 1558.8),
                ("五", 1558.8, 1559.0), ("倍", 1559.0, 1559.3), ("之", 1559.3, 1559.5), ("多", 1559.5, 1559.6),
                ("所", 1559.6, 1559.8), ("以", 1559.8, 1560.0), ("預", 1560.0, 1560.8), ("算", 1560.8, 1561.64),
                ("我", 1561.64, 1561.8), ("們", 1561.8, 1562.0), ("購", 1562.0, 1562.4), ("物", 1562.4, 1562.8),
                ("節", 1562.8, 1563.2), ("多", 1563.2, 1563.6), ("少", 1563.6, 1563.92),
            ])},
        ])
        raw = (
            "[25:41 - 25:59] **Speaker 4**: 五百零八倍\n\n"
            "[25:58 - 26:04] **Speaker 1**: 是它的五倍之多所以預算我們購物節多少\n\n"
            "[26:04 - 26:05] **Speaker 4**: 啊？"
        )
        fake = (
            "[25:41 - 25:59] **Speaker 4**: 五百零八倍\n"
            "[25:58 - 26:04] **Speaker 1**: <SPEAKER_SPLIT: Speaker 4> 是它的五倍之多所以預算 <SPEAKER_SPLIT: Speaker 1> 我們購物節多少\n"
            "[26:04 - 26:05] **Speaker 4**: 啊？"
        )
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 3, lines)
        # End uses ceil (1561.64 -> 26:02); the next start uses floor (26:01).
        self.assertTrue(lines[0].startswith("[25:41 - 26:02] **Speaker 4**:"), lines[0])
        self.assertIn("五百零八倍是它的五倍之多所以預算", lines[0])
        self.assertTrue(lines[1].startswith("[26:01 - 26:04] **Speaker 1**: 我們購物節多少"), lines[1])
        self.assertTrue(lines[2].startswith("[26:04 - 26:05] **Speaker 4**:"), lines[2])
        self.assertNotIn("SPEAKER_SPLIT", "\n".join(lines))

    def test_whole_line_relabel(self):
        raw = (
            "[26:01 - 26:04] **Speaker 1**: 獎品預算只有多少？\n\n"
            "[26:04 - 26:05] **Speaker 1**: 啊？\n\n"
            "[26:06 - 26:08] **Speaker 1**: 只有六百萬？\n\n"
            "[26:07 - 26:09] **Speaker 4**: 六百萬。"
        )
        fake = (
            "[26:01 - 26:04] **Speaker 1**: 獎品預算只有多少？\n"
            "[26:04 - 26:05] **Speaker 1**: <SPEAKER_SPLIT: Speaker 4> 啊？\n"
            "[26:06 - 26:08] **Speaker 1**: 只有六百萬？\n"
            "[26:07 - 26:09] **Speaker 4**: 六百萬。"
        )
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[1], "[26:04 - 26:05] **Speaker 4**: 啊？")

    def test_unknown_speaker_marker_is_ignored(self):
        raw = "[00:01 - 00:05] **Speaker 1**: Hello there, how are you today?"
        fake = "[00:01 - 00:05] **Speaker 1**: Hello there, <SPEAKER_SPLIT: Speaker 9> how are you today?"
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "[00:01 - 00:05] **Speaker 1**: Hello there, how are you today?")

    def test_split_and_para_on_same_line(self):
        self._seed_cache([
            {"start": 0.0, "end": 12.0, "word_items": _words([
                ("Alpha", 0.0, 2.0), ("beta", 2.0, 4.0), ("gamma", 4.0, 6.0), ("delta", 6.0, 8.0),
                ("epsilon", 8.0, 10.0), ("zeta", 10.0, 12.0),
            ])},
        ])
        raw = (
            "[00:00 - 00:12] **Speaker 1**: Alpha beta gamma delta epsilon zeta\n\n"
            "[00:12 - 00:14] **Speaker 2**: Ok"
        )
        fake = (
            "[00:00 - 00:12] **Speaker 1**: Alpha beta <PARA> gamma delta <SPEAKER_SPLIT: Speaker 2> epsilon zeta\n"
            "[00:12 - 00:14] **Speaker 2**: Ok"
        )
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(lines[0].startswith("[00:00 - 00:04] **Speaker 1**: Alpha beta"))
        self.assertTrue(lines[1].startswith("[00:04 - 00:08] **Speaker 1**: gamma delta"))
        self.assertTrue(lines[2].startswith("[00:08 - 00:12] **Speaker 2**: epsilon zeta"))

    def test_para_reprojection_uses_floor_ceil_cache_key(self):
        # Turn start 60.7 -> line "01:00" (floor); end 70.3 -> "01:11" (ceil).
        self._seed_cache([
            {"start": 60.7, "end": 70.3, "word_items": _words([
                ("One", 60.7, 62.0), ("two", 62.0, 64.0), ("three", 64.0, 66.0), ("four", 66.0, 70.3),
            ])},
        ])
        raw = "[01:00 - 01:11] **Speaker 1**: One two three four"
        fake = "[01:00 - 01:11] **Speaker 1**: One two <PARA> three four"
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 2)
        # Physical boundary at 64.0 (not an interpolated midpoint of 65.5).
        self.assertTrue(lines[0].startswith("[01:00 - 01:04]"), lines[0])
        self.assertTrue(lines[1].startswith("[01:04 - 01:11]"), lines[1])


if __name__ == "__main__":
    unittest.main()
