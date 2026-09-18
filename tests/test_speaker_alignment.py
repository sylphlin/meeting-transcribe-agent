# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Unit tests for the Speaker Alignment Architecture and Implementation Spec.
Validates:
1. parse_timestamp_to_seconds (robust time string parsing)
2. parse_srt_timeline (embedded subtitle stream parsing and name extraction)
3. generate_auto_outline_from_srt (meeting structure & agenda generation)
4. parse_scoped_speaker_mapping (handling acoustic under-clustering via time-scoped rules)
5. consolidate_verbatim_transcript (3-level hierarchical alignment & text preservation)
"""

import unittest
from pathlib import Path
import tempfile
import shutil

from scripts.canonicalizer import (
    parse_timestamp_to_seconds,
    parse_srt_timeline,
    generate_auto_outline_from_srt,
    parse_scoped_speaker_mapping,
    consolidate_verbatim_transcript,
    consolidate_meeting_minutes,
)


class TestTimestampParsing(unittest.TestCase):
    def test_mm_ss_format(self):
        self.assertEqual(parse_timestamp_to_seconds("00:00"), 0.0)
        self.assertEqual(parse_timestamp_to_seconds("01:23"), 83.0)
        self.assertEqual(parse_timestamp_to_seconds("59:59"), 3599.0)

    def test_hh_mm_ss_format(self):
        self.assertEqual(parse_timestamp_to_seconds("01:00:00"), 3600.0)
        self.assertEqual(parse_timestamp_to_seconds("01:02:03"), 3723.0)

    def test_millisecond_fractions(self):
        self.assertAlmostEqual(parse_timestamp_to_seconds("00:01:23,456"), 83.456, places=3)
        self.assertAlmostEqual(parse_timestamp_to_seconds("00:01:23.456"), 83.456, places=3)

    def test_invalid_formats(self):
        self.assertEqual(parse_timestamp_to_seconds(""), 0.0)
        self.assertEqual(parse_timestamp_to_seconds("invalid"), 0.0)
        self.assertEqual(parse_timestamp_to_seconds(None), 0.0)


class TestSrtTimelineParsing(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.srt_path = Path(self.test_dir) / "test_captions.srt"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_webrtc_captions(self):
        srt_content = """1
00:00:01,000 --> 00:00:05,000
[Alice Smith]: Welcome everyone to the quarterly review.

2
00:00:06,500 --> 00:00:12,000
<i>[Bob Jones]</i>: Thank you Alice. Let me share my screen.

3
00:00:13,000 --> 00:00:15,000
(Carol White): Loud and clear.

4
00:00:16,000 --> 00:00:20,000
[ ]: Noise in background.
"""
        self.srt_path.write_text(srt_content, encoding="utf-8")
        events = parse_srt_timeline(self.srt_path)

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["name"], "Alice Smith")
        self.assertAlmostEqual(events[0]["start"], 1.0)
        self.assertAlmostEqual(events[0]["end"], 5.0)

        self.assertEqual(events[1]["name"], "Bob Jones")
        self.assertAlmostEqual(events[1]["start"], 6.5)
        self.assertAlmostEqual(events[1]["end"], 12.0)

        self.assertEqual(events[2]["name"], "Carol White")
        self.assertAlmostEqual(events[2]["start"], 13.0)
        self.assertAlmostEqual(events[2]["end"], 15.0)

    def test_generate_auto_outline(self):
        srt_content = """1
00:00:01,000 --> 00:00:05,000
[Alice Smith]: Opening remarks.

2
00:07:30,000 --> 00:07:35,000
[Bob Jones]: Architecture deep dive.
"""
        self.srt_path.write_text(srt_content, encoding="utf-8")
        out_path = Path(self.test_dir) / "outline.md"
        result_path = generate_auto_outline_from_srt(self.srt_path, out_path)
        self.assertTrue(result_path.exists())

        content = result_path.read_text(encoding="utf-8")
        # Rule 4: Plain text typography without decorative emojis
        self.assertNotIn("📌", content)
        self.assertNotIn("🎯", content)
        self.assertIn("Alice Smith", content)
        self.assertIn("Bob Jones", content)
        self.assertIn("00:01", content)
        self.assertIn("07:30", content)


class TestScopedSpeakerMapping(unittest.TestCase):
    def test_standard_speaker_mapping(self):
        markdown = """## 1. Meeting Metadata & Attendees
- **Speaker Mapping Table**:
  | Speaker ID | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- |
  | `spk_0` | Meeting Host | Alice Smith | Executive Board |
  | `spk_1` | Tech Lead | Bob Jones | Architecture Dept |
"""
        rules = parse_scoped_speaker_mapping(markdown)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["spk_id"], "spk_0")
        self.assertEqual(rules[0]["name"], "Alice Smith (Meeting Host)")
        self.assertEqual(rules[0]["start"], 0.0)
        self.assertEqual(rules[0]["end"], float('inf'))

    def test_acoustic_underclustering_scoped_mapping(self):
        """
        When spk_0 is shared across two different speakers at different timestamps,
        parse_scoped_speaker_mapping should parse distinct time-bounded rules.
        """
        markdown = """## 1. Meeting Metadata & Attendees
- **Speaker Mapping Table**:
  | Speaker ID | Time Range | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- | :--- |
  | `spk_0` | `00:00 - 00:04` | Meeting Host | Alice Smith | Executive Board |
  | `spk_0` | `07:35 - 10:20` | Keynote Speaker | Bob Jones | Architecture Dept |
"""
        rules = parse_scoped_speaker_mapping(markdown)
        self.assertEqual(len(rules), 2)

        # Rule 1 for spk_0
        self.assertEqual(rules[0]["spk_id"], "spk_0")
        self.assertEqual(rules[0]["name"], "Alice Smith (Meeting Host)")
        self.assertAlmostEqual(rules[0]["start"], 0.0)
        self.assertAlmostEqual(rules[0]["end"], 4.0)

        # Rule 2 for spk_0
        self.assertEqual(rules[1]["spk_id"], "spk_0")
        self.assertEqual(rules[1]["name"], "Bob Jones (Keynote Speaker)")
        self.assertAlmostEqual(rules[1]["start"], 455.0)  # 07:35 = 7*60 + 35 = 455s
        self.assertAlmostEqual(rules[1]["end"], 620.0)    # 10:20 = 10*60 + 20 = 620s


class TestHierarchicalSpeakerAlignment(unittest.TestCase):
    def test_level_1_srt_ground_truth_alignment(self):
        """
        Level 1: Subtitle timestamps strictly provide speaker ground truth
        while verbatim text and physical timestamps come 100% from Stage 1 ASR.
        """
        events = [
            {"name": "Alice Smith", "start": 0.0, "end": 6.0, "text": "Low quality subtitle text"},
            {"name": "Bob Jones", "start": 7.0, "end": 15.0, "text": "Another subtitle text"},
        ]
        acoustic_transcript = (
            "[00:01 - 00:05] **spk_0**: Good morning everyone, high fidelity acoustic text.\n\n"
            "[00:08 - 00:14] **spk_0**: Thank you, proceeding with architecture slide."
        )

        result = consolidate_verbatim_transcript(
            transcript_text=acoustic_transcript,
            timeline_events=events,
        )

        # Spoken text is preserved exactly from acoustic transcript
        self.assertIn("[00:01 - 00:05] **Alice Smith**: Good morning everyone, high fidelity acoustic text.", result)
        self.assertIn("[00:08 - 00:14] **Bob Jones**: Thank you, proceeding with architecture slide.", result)
        # Low quality subtitle text is never injected into verbatim transcript
        self.assertNotIn("Low quality subtitle text", result)
        self.assertNotIn("Another subtitle text", result)

    def test_level_2_multimodal_scoped_rules_underclustering(self):
        """
        Level 2: When no SRT exists, scoped rules resolve acoustic under-clustering
        where spk_0 is Alice Smith early in the meeting and Bob Jones later in the meeting.
        """
        scoped_rules = [
            {
                "spk_id": "spk_0",
                "start": 0.0,
                "end": 30.0,
                "name": "Alice Smith (Meeting Host)",
                "raw_name": "Alice Smith",
            },
            {
                "spk_id": "spk_0",
                "start": 450.0,
                "end": 600.0,
                "name": "Bob Jones (Keynote Speaker)",
                "raw_name": "Bob Jones",
            },
        ]
        acoustic_transcript = (
            "[00:01 - 00:05] **spk_0**: Opening remarks by the host.\n\n"
            "[08:00 - 08:30] **spk_0**: Technical keynote presentation."
        )

        result = consolidate_verbatim_transcript(
            transcript_text=acoustic_transcript,
            scoped_rules=scoped_rules,
        )

        self.assertIn("[00:01 - 00:05] **Alice Smith (Meeting Host)**: Opening remarks by the host.", result)
        self.assertIn("[08:00 - 08:30] **Bob Jones (Keynote Speaker)**: Technical keynote presentation.", result)

    def test_level_3_forward_handover_calibration(self):
        """
        Level 3: Forward conversational handover resolves speaker turn when
        speaker A says 'over to you, Bob' and turn i+1 was unmapped.
        """
        acoustic_transcript = (
            "[00:01 - 00:05] **Alice Smith**: Great progress on the rollout. Now over to you, Bob.\n\n"
            "[00:06 - 00:10] **spk_1**: Thanks Alice, I will demonstrate the live metrics."
        )
        speaker_mapping = {
            "spk_0": "Alice Smith",
            "spk_1": "Bob Jones (Tech Lead)",
        }

        result = consolidate_verbatim_transcript(
            transcript_text=acoustic_transcript,
            speaker_mapping=speaker_mapping,
        )

        self.assertIn("**Alice Smith**: Great progress on the rollout. Now over to you, Bob.", result)
        self.assertIn("**Bob Jones (Tech Lead)**: Thanks Alice, I will demonstrate the live metrics.", result)

    def test_level_3_forward_handover_clears_erroneous_self_assignment(self):
        """
        If speaker A is mistakenly assigned Bob's identity while handing over to Bob,
        Level 3 detects the conflict and clears speaker A's identity.
        """
        acoustic_transcript = (
            "[00:01 - 00:05] **Bob Jones**: That concludes my section. I will pass it to Bob now.\n\n"
            "[00:06 - 00:10] **spk_unknown**: Thanks, glad to be here."
        )
        speaker_mapping = {
            "spk_bob": "Bob Jones",
        }

        result = consolidate_verbatim_transcript(
            transcript_text=acoustic_transcript,
            speaker_mapping=speaker_mapping,
        )

        # The first turn's speaker should be cleared to prevent self-handover contradiction
        self.assertIn("[00:01 - 00:05] That concludes my section. I will pass it to Bob now.", result)
        self.assertIn("**Bob Jones**: Thanks, glad to be here.", result)


class TestFullPipelineConsolidation(unittest.TestCase):
    def test_end_to_end_meeting_minutes_consolidation(self):
        markdown_input = """## 1. Meeting Metadata & Attendees
- **Meeting Title**: Global Architecture Forum
- **Speaker Mapping Table**:
  | Speaker ID | Time Range | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- | :--- |
  | `spk_0` | `00:00 - 00:05` | Forum Host | Alice Smith | Executive Board |
  | `spk_0` | `05:00 - 07:00` | Principal Architect | Bob Jones | Architecture Dept |

- **Phonetic & Entity Corrections Table**:
  | Mistranscribed Term | Corrected Name / Term | Target Speaker / Context |
  | :--- | :--- | :--- |
  | VertexAI | Vertex AI | Product Name |

## 2. Executive Summary
Overview of architectural decisions.

## 6. Full Verbatim Transcript

[00:01 - 00:04] **spk_0**: Welcome to the forum. Let's discuss VertexAI deployment.

[05:10 - 05:30] **spk_0**: The cloud infrastructure scaling is complete.
"""
        result = consolidate_meeting_minutes(markdown_input)

        # Alice mapped at 00:01 - 00:04
        self.assertIn("[00:01 - 00:04] **Alice Smith (Forum Host)**: Welcome to the forum. Let's discuss Vertex AI deployment.", result)
        # Bob mapped at 05:10 - 05:30
        self.assertIn("[05:10 - 05:30] **Bob Jones (Principal Architect)**: The cloud infrastructure scaling is complete.", result)
        # Phonetic entity correction applied
        self.assertIn("Vertex AI deployment", result)
        self.assertNotIn("VertexAI deployment", result)


if __name__ == "__main__":
    unittest.main()
