import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from gloss.visual.ocr import OcrError, WindowsOcr, ocr_metrics


def _fake_runner(returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(
            args=command, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return run, calls


def _payload(**overrides):
    payload = {
        "backend": "windows-media-ocr",
        "language": "en-US",
        "text": "Hello world",
        "lines": [{"text": "Hello world"}],
        "imageWidth": 800,
        "imageHeight": 200,
    }
    payload.update(overrides)
    return json.dumps(payload)


class WindowsOcrTest(unittest.TestCase):
    def _ocr(self, run, temp_dir: Path, language: str | None = None) -> tuple[WindowsOcr, Path]:
        script = temp_dir / "ocr.ps1"
        script.write_text("# fake", encoding="utf-8")
        image = temp_dir / "capture.png"
        image.write_bytes(b"\x89PNG fake")
        return (
            WindowsOcr(script_path=script, language=language, run=run),
            image,
        )

    def test_recognize_parses_lines(self) -> None:
        run, calls = _fake_runner(stdout=_payload(lines=[{"text": "Line A"}, {"text": "Line B"}]))
        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir))
            with contextlib.redirect_stderr(io.StringIO()):
                result = ocr.recognize(image)

        self.assertEqual(result.text, "Line A\nLine B")
        self.assertEqual(result.lines, ["Line A", "Line B"])
        self.assertEqual(result.language, "en-US")
        self.assertIn("-Image", calls[0])

    def test_recognize_collapses_spaces_for_japanese(self) -> None:
        run, _calls = _fake_runner(
            stdout=_payload(language="ja", lines=[{"text": "古い 町 に 月光"}])
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir), language="ja")
            with contextlib.redirect_stderr(io.StringIO()):
                result = ocr.recognize(image)

        self.assertEqual(result.text, "古い町に月光")

    def test_recognize_error_uses_stderr(self) -> None:
        run, _calls = _fake_runner(returncode=1, stderr="[ERROR] Windows OCR failed: boom")
        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir))
            with self.assertRaises(OcrError) as ctx:
                ocr.recognize(image)

        self.assertIn("boom", str(ctx.exception))

    def test_recognize_invalid_json(self) -> None:
        run, _calls = _fake_runner(stdout="not json")
        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir))
            with self.assertRaises(OcrError):
                ocr.recognize(image)

    def test_recognize_missing_image(self) -> None:
        run, calls = _fake_runner(stdout=_payload())
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "ocr.ps1"
            script.write_text("# fake", encoding="utf-8")
            ocr = WindowsOcr(script_path=script, run=run)
            with self.assertRaises(OcrError):
                ocr.recognize(Path(temp_dir) / "missing.png")

        self.assertEqual(calls, [])

    def test_list_languages(self) -> None:
        run, calls = _fake_runner(
            stdout=json.dumps(
                {
                    "backend": "windows-media-ocr",
                    "availableLanguages": [{"tag": "ko", "displayName": "Korean"}],
                }
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "ocr.ps1"
            script.write_text("# fake", encoding="utf-8")
            ocr = WindowsOcr(script_path=script, run=run)
            languages = ocr.list_languages()

        self.assertEqual(languages, [{"tag": "ko", "displayName": "Korean"}])
        self.assertIn("-ListLanguages", calls[0])

    def test_recognize_timeout_raises_ocr_error(self) -> None:
        seen: dict = {}

        def run(command, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))

        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir))
            with self.assertRaises(OcrError) as ctx:
                ocr.recognize(image)

        self.assertEqual(seen.get("timeout"), 60.0)
        self.assertIn("timed out after 60.0s", str(ctx.exception))

    def test_default_script_path_is_cwd_independent(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                ocr = WindowsOcr()
                self.assertTrue(ocr.script_path.is_file(), str(ocr.script_path))
            finally:
                os.chdir(original_cwd)

    def test_ocr_metrics_shape(self) -> None:
        run, _calls = _fake_runner(stdout=_payload())
        with tempfile.TemporaryDirectory() as temp_dir:
            ocr, image = self._ocr(run, Path(temp_dir))
            with contextlib.redirect_stderr(io.StringIO()):
                result = ocr.recognize(image)

        metrics = ocr_metrics(result)
        self.assertEqual(metrics["backend"], "windows-media-ocr")
        self.assertEqual(metrics["lineCount"], 1)
        self.assertGreaterEqual(metrics["elapsed_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
