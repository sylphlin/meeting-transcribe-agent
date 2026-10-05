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
        # Turn A (Speaker 2) ends at 118.2 -> "01:59" (ceil).
        # Turn B is mislabeled Speaker 1: it holds Speaker 2 words until 121.64, then Speaker 1 words.
        self._seed_cache([
            {"start": 101.0, "end": 118.2, "word_items": _words([("甲乙丙丁", 115.6, 118.2)])},
            {"start": 118.2, "end": 123.92, "word_items": _words([
                ("一", 118.2, 118.4), ("二", 118.4, 118.6), ("三", 118.6, 118.8),
                ("四", 118.8, 119.0), ("五", 119.0, 119.3), ("六", 119.3, 119.5), ("七", 119.5, 119.6),
                ("八", 119.6, 119.8), ("九", 119.8, 120.0), ("十", 120.0, 120.8), ("完", 120.8, 121.64),
                ("請", 121.64, 121.8), ("問", 121.8, 122.0), ("經", 122.0, 122.4), ("費", 122.4, 122.8),
                ("多", 122.8, 123.2), ("少", 123.2, 123.6), ("呢", 123.6, 123.92),
            ])},
        ])
        raw = (
            "[01:41 - 01:59] **Speaker 2**: 甲乙丙丁\n\n"
            "[01:58 - 02:04] **Speaker 1**: 一二三四五六七八九十完請問經費多少呢\n\n"
            "[02:04 - 02:05] **Speaker 2**: 啊？"
        )
        fake = (
            "[01:41 - 01:59] **Speaker 2**: 甲乙丙丁\n"
            "[01:58 - 02:04] **Speaker 1**: <SPEAKER_SPLIT: Speaker 2> 一二三四五六七八九十完 <SPEAKER_SPLIT: Speaker 1> 請問經費多少呢\n"
            "[02:04 - 02:05] **Speaker 2**: 啊？"
        )
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 3, lines)
        # End uses ceil (121.64 -> 02:02); the next start uses floor (02:01).
        self.assertTrue(lines[0].startswith("[01:41 - 02:02] **Speaker 2**:"), lines[0])
        self.assertIn("甲乙丙丁一二三四五六七八九十完", lines[0])
        self.assertTrue(lines[1].startswith("[02:01 - 02:04] **Speaker 1**: 請問經費多少呢"), lines[1])
        self.assertTrue(lines[2].startswith("[02:04 - 02:05] **Speaker 2**:"), lines[2])
        self.assertNotIn("SPEAKER_SPLIT", "\n".join(lines))

    def test_whole_line_relabel(self):
        raw = (
            "[02:01 - 02:04] **Speaker 1**: 請問經費多少呢？\n\n"
            "[02:04 - 02:05] **Speaker 1**: 啊？\n\n"
            "[02:06 - 02:08] **Speaker 1**: 只有這些嗎？\n\n"
            "[02:07 - 02:09] **Speaker 2**: 是的。"
        )
        fake = (
            "[02:01 - 02:04] **Speaker 1**: 請問經費多少呢？\n"
            "[02:04 - 02:05] **Speaker 1**: <SPEAKER_SPLIT: Speaker 2> 啊？\n"
            "[02:06 - 02:08] **Speaker 1**: 只有這些嗎？\n"
            "[02:07 - 02:09] **Speaker 2**: 是的。"
        )
        lines = self._run(raw, fake)
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[1], "[02:04 - 02:05] **Speaker 2**: 啊？")

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
