import os
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from google.genai import errors as genai_errors
from scripts.gemini_engine import call_gemini_with_retry, generate_minutes_with_gemini
from scripts.audio_utils import find_silence_cut_points


class TestGeminiRetry(unittest.TestCase):
    def test_success_first_attempt(self):
        mock_fn = MagicMock(return_value="success")
        res = call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(res, "success")
        self.assertEqual(mock_fn.call_count, 1)

    def test_retry_on_429_resource_exhausted(self):
        err = genai_errors.APIError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}}, None)
        mock_fn = MagicMock(side_effect=[err, "success_after_retry"])
        res = call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(res, "success_after_retry")
        self.assertEqual(mock_fn.call_count, 2)

    def test_retry_on_503_overloaded(self):
        err = genai_errors.APIError(503, {"error": {"message": "The service is overloaded"}}, None)
        mock_fn = MagicMock(side_effect=[err, "recovered"])
        res = call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(res, "recovered")
        self.assertEqual(mock_fn.call_count, 2)

    def test_retry_on_network_timeout(self):
        mock_fn = MagicMock(side_effect=[TimeoutError("Connection timed out"), "ok"])
        res = call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(res, "ok")
        self.assertEqual(mock_fn.call_count, 2)

    def test_fail_fast_on_401_unauthorized(self):
        err = genai_errors.APIError(401, {"error": {"message": "Unauthorized"}}, None)
        mock_fn = MagicMock(side_effect=err)
        with self.assertRaises(genai_errors.APIError) as ctx:
            call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(mock_fn.call_count, 1)

    def test_fail_fast_on_403_permission_denied(self):
        err = genai_errors.APIError(403, {"error": {"message": "AccessDeniedException"}}, None)
        mock_fn = MagicMock(side_effect=err)
        with self.assertRaises(genai_errors.APIError) as ctx:
            call_gemini_with_retry(mock_fn, max_retries=3, initial_delay=0.01)
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(mock_fn.call_count, 1)


class TestSilenceChunking(unittest.TestCase):
    def test_short_audio_no_split(self):
        cuts = find_silence_cut_points(Path("dummy.mp3"), total_duration=600.0, max_chunk_sec=840.0)
        self.assertEqual(cuts, [(0.0, 600.0)])

    def test_long_audio_fallback_split(self):
        total_dur = 2000.0  # ~33 minutes
        cuts = find_silence_cut_points(Path("nonexistent.mp3"), total_duration=total_dur, max_chunk_sec=840.0, search_window_sec=60.0)
        self.assertTrue(len(cuts) >= 3)
        self.assertEqual(cuts[0][0], 0.0)
        self.assertEqual(cuts[-1][1], total_dur)
        for i in range(len(cuts) - 1):
            self.assertEqual(cuts[i][1], cuts[i+1][0])
            dur = cuts[i][1] - cuts[i][0]
            self.assertLessEqual(dur, 840.0)


class TestStage2VerbatimAssembly(unittest.TestCase):
    @patch("scripts.gemini_engine.call_gemini_with_retry")
    def test_generate_minutes_deterministic_assembly(self, mock_retry):
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = """## 1. 會議基本資訊與出席人員
- **會議名稱**: 專案規劃會議
- **講者對照表**:
  | Speaker ID | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- |
  | `spk_0, spk_50` | 總經理 | 林大同 | 營運處 |
  | `spk_1` | 產品長 | 陳小芬 | 產品處 |

## 2. 執行摘要
本次會議聚焦於第四季產品佈署時程與系統架構優化。

## 3. 核心討論議題
- **議題一**: 產品時程規劃

## 4. 決議事項
- 決議下週三完成壓力測試。

## 5. 待辦事項與指派追蹤
| # | 待辦事項 | 負責人 | 期限 | 狀態 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 完成測試 | 陳小芬 | 下週三 | 進行中 |

## 6. 完整逐字記錄
"""
        mock_retry.return_value = mock_gemini_response

        raw_transcript = (
            "[00:01 - 00:05] **spk_0**: 各位同仁早安，我們今天開始討論專案進度。\n\n"
            "[00:06 - 00:10] **spk_1**: 好的，產品端已經準備就緒。\n\n"
            "[15:00 - 15:05] **spk_50**: 非常好，請按照計畫推動。"
        )

        mock_client = MagicMock()
        final_text, dur = generate_minutes_with_gemini(
            client=mock_client,
            audio_path=Path("meeting.mp3"),
            raw_transcript_text=raw_transcript,
            global_glossary="",
        )

        self.assertIn("## 6. 完整逐字記錄", final_text)
        self.assertIn("**林大同 (總經理)**: 各位同仁早安，我們今天開始討論專案進度。", final_text)
        self.assertIn("**陳小芬 (產品長)**: 好的，產品端已經準備就緒。", final_text)
        self.assertIn("**林大同 (總經理)**: 非常好，請按照計畫推動。", final_text)

        sec6_part = final_text.split("## 6. 完整逐字記錄")[1]
        self.assertNotIn("spk_0", sec6_part)
        self.assertNotIn("spk_50", sec6_part)
        self.assertNotIn("spk_1", sec6_part)

    @patch("scripts.gemini_engine.call_gemini_with_retry")
    def test_entity_corrections_and_handover_alignment(self, mock_retry):
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = """## 1. Meeting Metadata & Attendees
- **Meeting Title**: Developer Relations Sync
- **Speaker Mapping Table**:
  | Speaker ID | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- |
  | `spk_0` | Meeting Chair | Dave Elliott | Cloud AI |
  | `spk_1` | AI Developer Relations | Sylph Lin | Cloud AI |

- **Phonetic & Entity Corrections Table**:
  | Mistranscribed Term | Corrected Name / Term | Target Speaker / Context |
  | :--- | :--- | :--- |
  | Yusuf | Sylph | Sylph Lin (Developer Relations Lead) |

## 2. Executive Summary
Overview of AI developer relations roadmap.

## 6. Full Verbatim Transcript
"""
        mock_retry.return_value = mock_gemini_response

        raw_transcript = (
            "[00:01 - 00:05] **spk_0**: Welcome everyone. Now over to you, Yusuf.\n\n"
            "[00:06 - 00:10] **spk_1**: Thanks Dave. I will cover the developer experience today."
        )

        mock_client = MagicMock()
        final_text, dur = generate_minutes_with_gemini(
            client=mock_client,
            audio_path=Path("meeting.mp3"),
            raw_transcript_text=raw_transcript,
            global_glossary="",
        )

        self.assertIn("**Dave Elliott (Meeting Chair)**: Welcome everyone. Now over to you, Sylph.", final_text)
        self.assertIn("**Sylph Lin (AI Developer Relations)**: Thanks Dave.", final_text)
        sec6_part = final_text.split("## 6. Full Verbatim Transcript")[1]
        self.assertNotIn("Yusuf", sec6_part)

    @patch("scripts.gemini_engine.call_gemini_with_retry")
    def test_defensive_section6_fallback(self, mock_retry):
        # Stage 2 response omitted the Section 6 heading
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = """## 1. Meeting Metadata & Attendees
- **Meeting Title**: Quick Standup
- **Speaker Mapping Table**:
  | Speaker ID | Role / Title | Name | Organization / Team |
  | :--- | :--- | :--- | :--- |
  | `spk_0` | Engineer | Alice | Core Team |

## 2. Executive Summary
Brief status check.
"""
        mock_retry.return_value = mock_gemini_response

        raw_transcript = "[00:01 - 00:05] **spk_0**: Working on tests today."

        mock_client = MagicMock()
        final_text, dur = generate_minutes_with_gemini(
            client=mock_client,
            audio_path=Path("meeting.mp3"),
            raw_transcript_text=raw_transcript,
            global_glossary="",
        )

        self.assertIn("## 6. Full Verbatim Transcript", final_text)
        self.assertIn("**Alice (Engineer)**: Working on tests today.", final_text)


if __name__ == "__main__":
    unittest.main()
