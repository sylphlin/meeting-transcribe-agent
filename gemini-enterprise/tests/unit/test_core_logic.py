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
Unit tests for the language-agnostic minutes/transcript post-processing
logic in app.core.canonicalizer and app.core.html_generator. These modules
have no external (Google Cloud / Gemini) dependencies, so they're exercised
directly here rather than through the agent or its tools.
"""

from app.core.canonicalizer import consolidate_meeting_minutes
from app.core.html_generator import extract_meeting_title


def test_consolidate_meeting_minutes_canonicalizes_speakers_and_preserves_turns() -> None:
    """
    Speaker IDs in the verbatim transcript should be replaced using the
    Speaker Mapping Table from Section 1, while each turn keeps its own
    timestamp and stays a separate line (turns are never merged into one
    another, so per-turn seek accuracy is never lost).
    """
    markdown = """# Meeting Minutes

## 1. Meeting Metadata
| Speaker ID | Role | Name | Org |
| --- | --- | --- | --- |
| spk_0 | Chair | Alice | Eng |

## 6. Full Verbatim Transcript

[00:00 - 00:05] **spk_0**: Good morning everyone.
[00:05 - 00:12] **spk_0**: Let's start with the roadmap update.

### Break

[00:12 - 00:20] **spk_1**: Thanks Alice, here is my update.
"""
    result = consolidate_meeting_minutes(markdown)

    # Speaker ID resolved to its canonical name from the mapping table.
    assert "**Chair**: Good morning everyone." in result
    assert "**Chair**: Let's start with the roadmap update." in result
    # Unmapped speaker IDs are left as-is rather than dropped.
    assert "**spk_1**: Thanks Alice, here is my update." in result

    # Turns are not merged: both Chair lines keep their own timestamps.
    assert "[00:00 - 00:05]" in result
    assert "[00:05 - 00:12]" in result

    # The "### Break" header stays between the two speakers' turns instead
    # of drifting to the wrong position.
    chair_idx = result.index("Let's start with the roadmap update.")
    break_idx = result.index("### Break")
    spk1_idx = result.index("Thanks Alice")
    assert chair_idx < break_idx < spk1_idx


def test_extract_meeting_title_is_language_agnostic() -> None:
    """
    Title extraction is structural (Section 1 is always numbered "1", and
    Meeting Title is always its first labeled field) rather than keyword-
    based, so it must work for languages with no dedicated keyword pattern.
    """
    korean_minutes = """## 1. 회의 메타데이터 및 참석자
- **회의 제목**: 3분기 로드맵 동기화
- **오디오 소스**: `meeting.mp3`
"""
    assert extract_meeting_title(korean_minutes) == "3분기 로드맵 동기화"

    vietnamese_minutes = """## 1. Siêu dữ liệu cuộc họp
- **Tiêu đề cuộc họp**: Đồng bộ lộ trình quý 3
- **Nguồn âm thanh**: `meeting.mp3`
"""
    assert extract_meeting_title(vietnamese_minutes) == "Đồng bộ lộ trình quý 3"

    english_minutes = """## 1. Meeting Metadata & Attendees
- **Meeting Title**: Q3 Roadmap Sync
- **Audio Source**: `meeting.mp3`
"""
    assert extract_meeting_title(english_minutes) == "Q3 Roadmap Sync"


def test_extract_meeting_title_returns_none_when_absent() -> None:
    assert extract_meeting_title("") is None
    assert extract_meeting_title("# Just a heading, no metadata section") is None
