"""
Unit tests for time_utils and automatic static-ffmpeg fallback resolution.
"""

import builtins
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "multicam-video-preprocessing" / "scripts"))

from modules.time_utils import ensure_ffmpeg_in_path


class TestEnsureFfmpegInPath(unittest.TestCase):
    """Verify automatic static-ffmpeg PATH fallback behavior."""

    def test_skips_static_ffmpeg_when_both_binaries_present(self):
        """Skip static_ffmpeg import when both ffmpeg and ffprobe exist on PATH."""
        mock_static = types.ModuleType("static_ffmpeg")
        mock_static.add_paths = MagicMock()

        with patch("modules.time_utils.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"):
            with patch.dict(sys.modules, {"static_ffmpeg": mock_static}):
                ensure_ffmpeg_in_path()

        mock_static.add_paths.assert_not_called()

    def test_adds_static_ffmpeg_paths_when_binary_missing(self):
        """Call static_ffmpeg.add_paths(weak=True) when either ffmpeg or ffprobe is missing."""
        scenarios = [
            {"ffmpeg": None, "ffprobe": "/usr/local/bin/ffprobe"},
            {"ffmpeg": "/usr/local/bin/ffmpeg", "ffprobe": None},
            {"ffmpeg": None, "ffprobe": None},
        ]
        for lookup in scenarios:
            with self.subTest(lookup=lookup):
                mock_static = types.ModuleType("static_ffmpeg")
                mock_static.add_paths = MagicMock()
                with patch("modules.time_utils.shutil.which", side_effect=lambda b, m=lookup: m[b]):
                    with patch.dict(sys.modules, {"static_ffmpeg": mock_static}):
                        ensure_ffmpeg_in_path()
                mock_static.add_paths.assert_called_once_with(weak=True)

    def test_handles_import_error_gracefully(self):
        """Do not raise when static_ffmpeg is not installed and binaries are missing."""
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "static_ffmpeg":
                raise ImportError("No module named 'static_ffmpeg'")
            return real_import(name, *args, **kwargs)

        with patch("modules.time_utils.shutil.which", return_value=None):
            with patch.dict(sys.modules, {"static_ffmpeg": None}):
                with patch("builtins.__import__", side_effect=_fake_import):
                    ensure_ffmpeg_in_path()


if __name__ == "__main__":
    unittest.main()
