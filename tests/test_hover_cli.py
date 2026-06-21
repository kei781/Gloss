from pathlib import Path
import tempfile
import unittest

from gloss.visual.hover_cli import _cleanup_capture_file


class HoverCliTest(unittest.TestCase):
    def test_cleanup_capture_file_removes_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            capture = Path(temp_dir) / "capture.png"
            capture.write_bytes(b"\x89PNG fake")

            _cleanup_capture_file(capture)

            self.assertFalse(capture.exists())

    def test_cleanup_capture_file_ignores_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _cleanup_capture_file(Path(temp_dir) / "missing.png")


if __name__ == "__main__":
    unittest.main()
