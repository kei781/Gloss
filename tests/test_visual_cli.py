import contextlib
from importlib.util import find_spec
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gloss.visual.cli import main


class VisualCliTest(unittest.TestCase):
    @unittest.skipUnless(find_spec("PIL"), "Pillow unavailable")
    def test_dry_run_vlm_image_writes_output(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "dialog.png"
            Image.new("RGB", (32, 16), "white").save(image)
            output = Path(temp_dir) / "output.txt"
            with contextlib.redirect_stderr(io.StringIO()):
                exit_code = main([
                    "--dry-run", "--image-file", str(image),
                    "--profile", "qwen3-vl-4b", "--output", str(output),
                    "--metrics", str(Path(temp_dir) / "metrics.jsonl"),
                ])
            self.assertEqual(exit_code, 0)
            self.assertIn("[image input]", output.read_text(encoding="utf-8"))

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
            with patch("gloss.visual.cli.make_screen_capture") as capture_class:
                exit_code = main(["--capture-rect", "0,0,100,100"])

        self.assertEqual(exit_code, 1)
        capture_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
