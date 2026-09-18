import unittest
from scripts.gcs_utils import is_gdrive_source, parse_gdrive_url


class TestGDriveUtils(unittest.TestCase):
    def test_is_gdrive_source(self):
        self.assertTrue(is_gdrive_source("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view"))
        self.assertTrue(is_gdrive_source("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertTrue(is_gdrive_source("gdrive://1AbCdEfGhIjKlMnOpQrStUvWxYz123456"))
        self.assertFalse(is_gdrive_source("/tmp/meeting.mp4"))
        self.assertFalse(is_gdrive_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))

    def test_parse_gdrive_url(self):
        self.assertEqual(
            parse_gdrive_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view?usp=sharing"),
            {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "file"},
        )
        self.assertEqual(
            parse_gdrive_url("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
            {"id": "1AbCdEfGhIjKlMnOpQrStUvWxYz123456", "type": "folder"},
        )


if __name__ == "__main__":
    unittest.main()
