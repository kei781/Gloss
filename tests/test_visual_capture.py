import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from gloss.visual.capture import (
    CaptureError, FallbackScreenCapture, PowerShellScreenCapture,
    WindowsGraphicsCapture, make_screen_capture,
)
from gloss.visual.models import CaptureResult, Rect


class PowerShellScreenCaptureTest(unittest.TestCase):
    def test_auto_uses_gdi_when_wgc_dependencies_missing(self) -> None:
        with mock.patch("gloss.visual.capture.find_spec", return_value=None):
            self.assertIsInstance(make_screen_capture("auto"), PowerShellScreenCapture)
            with self.assertRaises(CaptureError):
                make_screen_capture("wgc")

    def test_auto_falls_back_when_wgc_fails(self) -> None:
        rect = Rect(0, 0, 20, 10)
        expected = CaptureResult(rect, Path("test.png"), "gdi", 0.1)
        primary = mock.Mock()
        primary.capture_rect.side_effect = CaptureError("no frame")
        fallback = mock.Mock()
        fallback.capture_rect.return_value = expected
        result = FallbackScreenCapture(primary, fallback).capture_rect(
            rect, output_dir=Path("captures")
        )
        self.assertEqual(result, expected)
        fallback.capture_rect.assert_called_once()

    def test_wgc_sends_region_and_writes_png(self) -> None:
        calls = {}

        class FakeFrame:
            shape = (10, 20, 4)

            def tobytes(self):
                return b"\0" * 800

        class FakeCamera:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def grab(self, **kwargs):
                calls["region"] = kwargs["region"]
                return FakeFrame()

        class FakeImage:
            def convert(self, mode):
                calls["mode"] = mode
                return self

            def save(self, path, format):
                calls["format"] = format
                path.write_bytes(b"PNG")

        dxcam = types.ModuleType("dxcam")
        dxcam.create = lambda **kwargs: FakeCamera()
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(
            frombytes=lambda *args: FakeImage()
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(sys.modules, {"dxcam": dxcam, "PIL": pil}):
                result = WindowsGraphicsCapture().capture_rect(
                    Rect(5, 6, 20, 10), output_dir=Path(temp_dir)
                )
            self.assertEqual(result.backend, "wgc-winrt")
            self.assertTrue(result.image_path.exists())
        self.assertEqual(calls["region"], (5, 6, 25, 16))
        self.assertEqual(calls["format"], "PNG")

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
