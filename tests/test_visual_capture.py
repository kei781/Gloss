import os
from pathlib import Path
import tempfile
import unittest

from gloss.visual.capture import PowerShellScreenCapture


class PowerShellScreenCaptureTest(unittest.TestCase):
    def test_default_script_path_is_cwd_independent(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                capture = PowerShellScreenCapture()
                self.assertTrue(capture.script_path.is_file(), str(capture.script_path))
                self.assertEqual(capture.script_path.name, "capture_screen_rect.ps1")
                self.assertIn("resources", capture.script_path.parts)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
