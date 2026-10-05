"""
tests/test_srt_speaker_parser.py - Universal subtitle speaker extraction and candidate resolution.

Covers:
1. Google Meet bracketed name on its own line, with unnamed continuation cues
2. Microsoft Teams WebVTT voice tags without index lines
3. Zoom / Webex colon-delimited tokens, including fullwidth colon
4. Rule-based guardrail: sound effects, URLs, notes rejected; names such as Isabella accepted
5. Injected speaker resolver: non-speaker tokens dropped, variants merged
6. Role column positional fallback when the LLM localizes the Section 1 header
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "meeting-transcribe-agent"))

from scripts.canonicalizer import (  # noqa: E402
    parse_srt_timeline,
    parse_scoped_speaker_mapping,
    consolidate_meeting_minutes,
)
from scripts.srt_speaker_resolver import (  # noqa: E402
    is_plausible_speaker_token,
    resolve_with_rules,
)


GOOGLE_MEET_SRT = """1
00:00:36,000 --> 00:00:40,000
(Jane Smith)
the first per second to ten second it should go to the camera

2
00:00:40,500 --> 00:00:44,000
one and then ten to twenty should go to camera two

3
00:00:50,000 --> 00:00:53,000
this cue is far away and has no name

4
00:00:53,500 --> 00:00:56,000
(John Doe)
thanks Jane, let me share my screen

5
00:00:56,200 --> 00:00:58,000
(applause)

6
00:00:58,100 --> 00:01:00,000
continuation after a sound effect
"""

TEAMS_VTT = """WEBVTT

00:01.000 --> 00:04.000 align:start position:0%
<v Jane Smith>Welcome everyone.</v>

00:04.500 --> 00:08.000
<v.loud John Doe>Thanks Jane.</v>
"""

ZOOM_SRT = """1
00:00:01,000 --> 00:00:04,000
Jane Smith: Welcome everyone.

2
00:00:04,500 --> 00:00:08,000
王小明：大家好。

3
00:00:08,500 --> 00:00:10,000
https://example.com/agenda

4
00:00:10,500 --> 00:00:12,000
Note: slides are shared in chat.
"""


class TestUniversalSubtitleExtraction(unittest.TestCase):
    def test_google_meet_bracket_on_own_line_with_continuation(self):
        events = parse_srt_timeline(GOOGLE_MEET_SRT)
        names = [(round(e["start"], 1), e["name"]) for e in events]
        # Cue 2 inherits Jane (gap 0.5s). Cue 3 is 6s away: no inheritance.
        # Cue 5 is a sound effect: dropped and breaks continuity, so cue 6 is not attributed.
        self.assertEqual(names, [(36.0, "Jane Smith"), (40.5, "Jane Smith"), (53.5, "John Doe")])

    def test_google_meet_without_inheritance(self):
        events = parse_srt_timeline(GOOGLE_MEET_SRT, inherit_previous_speaker=False)
        self.assertEqual([e["name"] for e in events], ["Jane Smith", "John Doe"])

    def test_teams_webvtt_voice_tags(self):
        events = parse_srt_timeline(TEAMS_VTT)
        self.assertEqual([e["name"] for e in events], ["Jane Smith", "John Doe"])
        self.assertAlmostEqual(events[0]["start"], 1.0)
        self.assertAlmostEqual(events[1]["end"], 8.0)

    def test_zoom_colon_tokens_and_url_note_rejection(self):
        events = parse_srt_timeline(ZOOM_SRT, inherit_previous_speaker=False)
        self.assertEqual([e["name"] for e in events], ["Jane Smith", "王小明"])

    def test_explicit_empty_tag_breaks_continuity(self):
        srt = """1
00:00:01,000 --> 00:00:03,000
[Jane Smith]: Hello.

2
00:00:03,200 --> 00:00:05,000
[ ]: Noise in background.
"""
        events = parse_srt_timeline(srt)
        self.assertEqual(len(events), 1)


class TestRuleGuardrail(unittest.TestCase):
    def test_names_containing_blacklist_substrings_are_accepted(self):
        for name in ("Isabella Campbell", "Soundarya Rao", "Leigh Singh"):
            self.assertTrue(is_plausible_speaker_token(name), name)

    def test_sound_effects_and_labels_are_rejected(self):
        for token in ("applause", "Music", "laughter", "inaudible", "Note", "e.g.", "https", "Slide 3"):
            self.assertFalse(is_plausible_speaker_token(token), token)

    def test_resolve_with_rules_contract(self):
        out = resolve_with_rules({"Jane Smith": ["hi"], "music": []})
        self.assertEqual(out["Jane Smith"], "Jane Smith")
        self.assertIsNone(out["music"])


class TestInjectedResolver(unittest.TestCase):
    def test_resolver_merges_variants_and_drops_non_speakers(self):
        srt = """1
00:00:01,000 --> 00:00:03,000
(Jane Smith)
Hello.

2
00:00:03,200 --> 00:00:05,000
(Jane Smith (she/her))
Still me.

3
00:00:05,200 --> 00:00:07,000
(Translator)
Interpreted audio.
"""
        seen = {}

        def resolver(candidates):
            seen.update(candidates)
            return {
                "Jane Smith": "Jane Smith",
                "Jane Smith (she/her)": "Jane Smith",
                "Translator": None,
            }

        events = parse_srt_timeline(srt, speaker_resolver=resolver)
        self.assertEqual(set(seen.keys()), {"Jane Smith", "Jane Smith (she/her)", "Translator"})
        self.assertEqual([e["name"] for e in events], ["Jane Smith", "Jane Smith"])

    def test_resolver_exception_falls_back_to_rules(self):
        def broken(candidates):
            raise RuntimeError("offline")

        events = parse_srt_timeline(ZOOM_SRT, speaker_resolver=broken, inherit_previous_speaker=False)
        self.assertEqual([e["name"] for e in events], ["Jane Smith", "王小明"])


class TestRoleColumnFallback(unittest.TestCase):
    LOCALIZED_TABLE = """## 1. 會議資訊與出席人員

| 發言標籤 | 時間區間 | 官職／身分 | 姓名 | 主管機關／服務單位 | 備註 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `Speaker 1` | 全程 | 市長（主席） | 黃偉哲 | 市政府 | 主持 |
| `Speaker 2` | 07:54 - 11:58 | 處長 | 方川和 | 動保處 | 報告 |
"""

    def test_localized_header_keeps_role(self):
        rules = parse_scoped_speaker_mapping(self.LOCALIZED_TABLE)
        by_id = {r["spk_id"]: r for r in rules}
        self.assertEqual(by_id["spk_1"]["name"], "黃偉哲 (市長（主席）)")
        self.assertEqual(by_id["spk_2"]["role"], "處長")

    def test_section6_speaker_includes_role(self):
        md = (
            self.LOCALIZED_TABLE
            + "\n## 6. 完整逐字記錄\n\n"
            + "[07:33 - 07:44] **Speaker 1**: 請問各位同仁。\n\n"
            + "[07:54 - 08:24] **Speaker 2**: 市長好。\n"
        )
        out = consolidate_meeting_minutes(md)
        self.assertIn("**黃偉哲 (市長（主席）)**", out)
        self.assertIn("**方川和 (處長)**", out)

    def test_unknown_name_dash_uses_role_only(self):
        md = (
            "## 1. Metadata\n\n"
            "| Speaker ID | Time Range | Role / Title | Name | Organization | Remarks |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
            "| `Speaker 0` | - | Master of Ceremonies | - | Secretariat | Reads the agenda |\n"
            "| `Speaker 1` | - | Mayor (Chair) | John Doe | City Hall | Chairs the meeting |\n\n"
            "## 6. Full Verbatim Transcript\n\n"
            "[00:01 - 00:03] **Speaker 0**: Item two.\n\n"
            "[00:04 - 00:09] **Speaker 1**: Thank you.\n"
        )
        out = consolidate_meeting_minutes(md)
        self.assertIn("**Master of Ceremonies**", out)
        self.assertIn("**John Doe (Mayor (Chair))**", out)

    def test_korean_header_parsed_by_position(self):
        md = (
            "| 화자 | 구간 | 직책 | 이름 | 소속 | 비고 |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
            "| `Speaker 2` | 01:00 - 02:00 | 팀장 | 김민수 | 개발팀 | 보고 |\n"
        )
        rules = parse_scoped_speaker_mapping(md)
        self.assertEqual(rules[0]["name"], "김민수 (팀장)")
        self.assertAlmostEqual(rules[0]["start"], 60.0)

    def test_subtitle_ground_truth_keeps_role(self):
        md = (
            "## 1. Meeting Metadata & Attendees\n\n"
            "| Speaker ID | Time Range | Role / Title | Name | Organization |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| `Speaker 1` | - | Host | Jane Smith | Example Corp |\n\n"
            "## 6. Full Verbatim Transcript\n\n"
            "[00:01 - 00:03] **Speaker 2**: Hello.\n"
        )
        srt = "1\n00:00:01,000 --> 00:00:03,000\n(Jane Smith)\nHello.\n"
        out = consolidate_meeting_minutes(md, srt_path=srt)
        self.assertIn("**Jane Smith (Host)**", out)


if __name__ == "__main__":
    unittest.main()


class TestEntityCorrectionsBlock(unittest.TestCase):
    MD = (
        "## 1. 會議基本資訊\n\n"
        "| 發言代碼 | 時間區間 | 職稱 | 姓名 | 機關 | 備註 |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| `Speaker 1` | - | 市長 | 黃偉哲 | 市政府 | 主席 |\n"
        "<!-- ENTITY_CORRECTIONS_START -->\n"
        "- **語音辨識名詞更正表**：\n"
        "  | 原音誤聽詞 | 更正後 | 語境 |\n"
        "  | :--- | :--- | :--- |\n"
        "  | 陳豪起 | 陳豪吉 | 專門委員 |\n"
        "<!-- ENTITY_CORRECTIONS_END -->\n\n"
        "## 2. 執行摘要\n\n內容。\n\n"
        "## 6. 完整逐字記錄\n\n"
        "[00:01 - 00:03] **Speaker 1**: 請陳豪起報告。\n"
    )

    def test_corrections_parsed_by_position_inside_markers(self):
        from scripts.canonicalizer import parse_entity_corrections_from_markdown
        self.assertEqual(parse_entity_corrections_from_markdown(self.MD), {"陳豪起": "陳豪吉"})

    def test_block_applied_then_removed_from_deliverable(self):
        out = consolidate_meeting_minutes(self.MD)
        self.assertIn("請陳豪吉報告", out)
        self.assertNotIn("ENTITY_CORRECTIONS", out)
        self.assertNotIn("語音辨識名詞更正表", out)
        self.assertNotIn("陳豪起", out)
        self.assertIn("## 2. 執行摘要", out)
        self.assertIn("**黃偉哲 (市長)**", out)
