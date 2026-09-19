import unittest
from scripts.gcs_utils import (
    is_gdrive_source,
    parse_gdrive_url,
    fix_mojibake_filename,
    extract_filename_from_content_disposition,
)
from scripts.gemini_engine import refine_verbatim_transcript_chunks


class TestGDriveUtils(unittest.TestCase):
    def test_is_gdrive_source(self):
        self.assertTrue(is_gdrive_source("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view"))
        self.assertTrue(is_gdrive_source("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertTrue(is_gdrive_source("gdrive://1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertFalse(is_gdrive_source("/tmp/meeting.mp4"))
        self.assertFalse(is_gdrive_source("https://www.youtube.com/watch?v=VIDEO_ID123"))

    def test_parse_gdrive_url(self):
        self.assertEqual(
            parse_gdrive_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view?usp=sharing"),
            {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "file"},
        )
        self.assertEqual(
            parse_gdrive_url("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
            {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "folder"},
        )

    def test_fix_mojibake_filename_and_content_disposition(self):
        original = "20260901市政會議專案簡報_128k.mp3"
        latin1_mojibake = original.encode("utf-8").decode("latin-1")
        self.assertEqual(fix_mojibake_filename(latin1_mojibake), original)
        self.assertEqual(fix_mojibake_filename(original), original)
        self.assertEqual(fix_mojibake_filename("meeting_recording.mp3"), "meeting_recording.mp3")

        cd_latin1 = f'attachment; filename="{latin1_mojibake}"'
        self.assertEqual(
            extract_filename_from_content_disposition(cd_latin1, "fallback.mp3"),
            original,
        )

        cd_rfc5987_lower = "attachment; filename=\"fallback.mp3\"; filename*=utf-8''20260901%E5%B8%82%E6%94%BF%E6%9C%83%E8%AD%B0%E5%B0%88%E6%A1%88%E7%B0%A1%E5%A0%B1_128k.mp3"
        self.assertEqual(
            extract_filename_from_content_disposition(cd_rfc5987_lower, "fallback.mp3"),
            original,
        )

    def test_refine_verbatim_transcript_chunks_script_alignment(self):
        raw_turns = (
            "[07:34 - 07:44] **spk_0**: 请问各位同仁对于上次会议议事录有没有疑问？\n"
            "[07:44 - 07:54] **spk_1**: 二，报告事项。请处长报告，时间五分钟。"
        )
        sections_1_5 = "## 1. 會議基本資訊\n- **會議名稱**: 專案進度會議\n## 2. 會議摘要\n進度報告。"

        def mock_track_fn(prompt_text: str, label: str) -> str:
            return (
                "[07:34 - 07:44] **spk_0**: 請問各位同仁對於上次會議議事錄有沒有疑問？\n"
                "[07:44 - 07:54] **spk_1**: 二，報告事項。請處長報告，時間五分鐘。"
            )

        refined = refine_verbatim_transcript_chunks(
            raw_transcript_text=raw_turns,
            sections_1_5=sections_1_5,
            global_glossary="",
            target_lang="Traditional Chinese",
            call_track_fn=mock_track_fn,
        )
        self.assertIn("[07:34 - 07:44] **spk_0**: 請問各位同仁對於上次會議議事錄有沒有疑問？", refined)
        self.assertIn("[07:44 - 07:54] **spk_1**: 二，報告事項。請處長報告，時間五分鐘。", refined)


if __name__ == "__main__":
    unittest.main()
