import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gloss.visual.cli import main


class VisualCliTest(unittest.TestCase):
    def test_dry_run_ocr_text_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = Path(temp_dir) / "metrics.jsonl"
            output = Path(temp_dir) / "visual.md"

            with contextlib.redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "--dry-run",
                        "--ocr-text",
                        "Visible text.",
                        "--metrics",
                        str(metrics),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Visible text.", output.read_text(encoding="utf-8"))

    def test_requires_ocr_text_without_vlm_backend(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code = main(["--dry-run"])

        self.assertEqual(exit_code, 1)

    def test_non_dry_run_requires_ocr_before_capture(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with patch("gloss.visual.cli.PowerShellScreenCapture") as capture_class:
                exit_code = main(["--capture-rect", "0,0,100,100"])

        self.assertEqual(exit_code, 1)
        capture_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
