import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_region_watch import RECT, FakeCapture, FakeOcr

from gloss.overlay.tk_overlay import OverlayGeometry
from gloss.visual import watch_cli
from gloss.visual.models import CaptureResult, VisualTranslation
from gloss.visual.ocr import OcrResult
from gloss.visual.watch import WatchError, WatchEvent, WatchSummary
from gloss.visual.watch_cli import _run_with_overlay, build_parser, main


class FakeController:
    def __init__(self, *, geometry, **_kwargs):
        self.geometry = geometry
        self.shown: list[str] = []
        self.closed = False

    def show(self, text: str) -> None:
        self.shown.append(text)

    def close(self) -> None:
        self.closed = True

    def run(self, worker) -> None:
        worker()


def _fake_event(text: str) -> WatchEvent:
    capture = CaptureResult(
        rect=RECT, image_path=Path("fake.png"), backend="fake", elapsed_s=0.0
    )
    ocr = OcrResult(
        text=text,
        lines=[text],
        language="en-US",
        backend="fake-ocr",
        elapsed_s=0.0,
        image_width=10,
        image_height=10,
    )
    return WatchEvent(
        iteration=1,
        ocr_text=text,
        translation=VisualTranslation(
            translated_text=f"KO:{text}", source_text=text, capture=capture
        ),
        capture=capture,
        ocr=ocr,
    )


class StubWatcher:
    def __init__(self, summary=None, error=None, event=None):
        self.on_event = None
        self._summary = summary
        self._error = error
        self._event = event

    def run(self):
        if self._event is not None and self.on_event is not None:
            self.on_event(self._event)
        if self._error is not None:
            raise self._error
        return self._summary


class WatchCliTest(unittest.TestCase):
    def test_watch_rect_required(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args([])

        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_rect_returns_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code = main(["--watch-rect", "10,10,0,0", "--dry-run"])

        self.assertEqual(exit_code, 1)

    def test_invalid_overlay_rect_returns_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code = main(
                ["--watch-rect", "0,0,100,50", "--dry-run", "--overlay", "--overlay-rect", "junk"]
            )

        self.assertEqual(exit_code, 1)

    def test_parser_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--watch-rect", "0,0,100,50"])

        self.assertEqual(args.interval, 2.0)
        self.assertEqual(args.stability, 0)
        self.assertEqual(args.ocr_backend, "windows")
        self.assertFalse(args.keep_captures)
        self.assertIsNone(args.max_iterations)

    def test_run_with_overlay_returns_summary_and_keeps_base_event(self) -> None:
        order: list[str] = []
        expected = WatchSummary(iterations=1, translations=1)
        watcher = StubWatcher(summary=expected, event=_fake_event("Hello"))
        watcher.on_event = lambda event: order.append(f"base:{event.ocr_text}")

        with mock.patch.object(watch_cli, "OverlayController", FakeController):
            with contextlib.redirect_stderr(io.StringIO()):
                summary = _run_with_overlay(
                    watcher, OverlayGeometry(0, 0, 100, 50)
                )

        self.assertIs(summary, expected)
        self.assertEqual(order, ["base:Hello"])

    def test_run_with_overlay_reraises_worker_error_and_closes(self) -> None:
        watcher = StubWatcher(error=WatchError("boom"))
        captured: dict = {}

        class RecordingController(FakeController):
            def __init__(self, *, geometry, **kwargs):
                super().__init__(geometry=geometry, **kwargs)
                captured["controller"] = self

        with mock.patch.object(watch_cli, "OverlayController", RecordingController):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(WatchError):
                    _run_with_overlay(watcher, OverlayGeometry(0, 0, 100, 50))

        self.assertTrue(captured["controller"].closed)

    def test_run_with_overlay_show_called_after_base_event(self) -> None:
        order: list[str] = []

        class RecordingController(FakeController):
            def show(self, text: str) -> None:
                order.append(f"show:{text}")
                super().show(text)

        watcher = StubWatcher(
            summary=WatchSummary(), event=_fake_event("Hello")
        )
        watcher.on_event = lambda event: order.append("base")

        with mock.patch.object(watch_cli, "OverlayController", RecordingController):
            with contextlib.redirect_stderr(io.StringIO()):
                _run_with_overlay(watcher, OverlayGeometry(0, 0, 100, 50))

        self.assertEqual(order, ["base", "show:KO:Hello"])

    def test_main_dry_run_wires_metrics_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metrics = root / "metrics.jsonl"
            output = root / "out.md"
            config = root / "config.json"
            config.write_text('{"backend": {"api_key": "local"}}', encoding="utf-8")

            with mock.patch.object(
                watch_cli,
                "PowerShellScreenCapture",
                lambda: FakeCapture([b"frame-a"]),
            ), mock.patch.object(
                watch_cli,
                "WindowsOcr",
                lambda language=None: FakeOcr({b"frame-a": "Hello"}),
            ):
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(
                    io.StringIO()
                ) as stdout:
                    exit_code = main(
                        [
                            "--watch-rect", "0,0,100,50",
                            "--dry-run",
                            "--max-iterations", "1",
                            "--config", str(config),
                            "--model", "test-model",
                            "--base-url", "http://127.0.0.1:11435/v1",
                            "--metrics", str(metrics),
                            "--output", str(output),
                            "--capture-output", str(root / "captures"),
                        ]
                    )

            self.assertEqual(exit_code, 0)
            row = json.loads(metrics.read_text(encoding="utf-8").splitlines()[0])
            written = output.read_text(encoding="utf-8")

        self.assertEqual(row["phase"], 3)
        self.assertEqual(row["inputMode"], "watch_ocr")
        self.assertEqual(row["watch"]["iteration"], 1)
        self.assertEqual(row["watch"]["interval_s"], 2.0)
        self.assertEqual(row["ocr"]["lineCount"], 1)
        # metrics_extra must not clobber the core row fields
        self.assertIn("capture", row)
        self.assertIn("generation", row)
        self.assertIn("[DRY RUN]", written)
        self.assertIn("Hello", written)
        # --output set -> stdout stays clean (convention shared with gloss-text)
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
