#!/usr/bin/env python3
"""Unit tests for static-ffmpeg fallback in ensure_ffmpeg_in_path()."""

import builtins
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "meeting-transcribe-agent"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.audio_utils import ensure_ffmpeg_in_path


class TestEnsureFfmpegInPath(unittest.TestCase):
    """Verify automatic static-ffmpeg PATH fallback behavior."""

    def test_skips_static_ffmpeg_when_both_binaries_exist(self) -> None:
        """Skip static_ffmpeg import when system ffmpeg and ffprobe exist on PATH."""
        mock_static_ffmpeg = types.ModuleType("static_ffmpeg")
        mock_static_ffmpeg.add_paths = MagicMock()

        def fake_which(cmd: str) -> str | None:
            return f"/usr/local/bin/{cmd}" if cmd in {"ffmpeg", "ffprobe"} else None

        with patch("scripts.audio_utils.shutil.which", side_effect=fake_which), patch.dict(
            sys.modules, {"static_ffmpeg": mock_static_ffmpeg}
        ):
            ensure_ffmpeg_in_path()

        mock_static_ffmpeg.add_paths.assert_not_called()

    def test_adds_static_ffmpeg_paths_when_ffmpeg_missing(self) -> None:
        """Call static_ffmpeg.add_paths(weak=True) when ffmpeg is missing from PATH."""
        mock_static_ffmpeg = types.ModuleType("static_ffmpeg")
        mock_static_ffmpeg.add_paths = MagicMock()

        def fake_which(cmd: str) -> str | None:
            return "/usr/local/bin/ffprobe" if cmd == "ffprobe" else None

        with patch("scripts.audio_utils.shutil.which", side_effect=fake_which), patch.dict(
            sys.modules, {"static_ffmpeg": mock_static_ffmpeg}
        ):
            ensure_ffmpeg_in_path()

        mock_static_ffmpeg.add_paths.assert_called_once_with(weak=True)

    def test_adds_static_ffmpeg_paths_when_ffprobe_missing(self) -> None:
        """Call static_ffmpeg.add_paths(weak=True) when ffprobe is missing from PATH."""
        mock_static_ffmpeg = types.ModuleType("static_ffmpeg")
        mock_static_ffmpeg.add_paths = MagicMock()

        def fake_which(cmd: str) -> str | None:
            return "/usr/local/bin/ffmpeg" if cmd == "ffmpeg" else None

        with patch("scripts.audio_utils.shutil.which", side_effect=fake_which), patch.dict(
            sys.modules, {"static_ffmpeg": mock_static_ffmpeg}
        ):
            ensure_ffmpeg_in_path()

        mock_static_ffmpeg.add_paths.assert_called_once_with(weak=True)

    def test_handles_import_error_gracefully(self) -> None:
        """Do not raise an exception when static_ffmpeg is not installed."""
        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "static_ffmpeg":
                raise ImportError("No module named 'static_ffmpeg'")
            return real_import(name, *args, **kwargs)

        with patch("scripts.audio_utils.shutil.which", return_value=None), patch.dict(
            sys.modules, {"static_ffmpeg": None}
        ), patch("builtins.__import__", side_effect=fake_import):
            ensure_ffmpeg_in_path()


if __name__ == "__main__":
    unittest.main()
