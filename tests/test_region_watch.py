import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from gloss.backend.openai_client import BackendError
from gloss.visual.capture import CaptureError
from gloss.visual.models import CaptureResult, Rect, VisualTranslation
from gloss.visual.ocr import OcrError, OcrResult
from gloss.visual.watch import RegionWatcher, WatchConfig, WatchError


RECT = Rect(x=0, y=0, width=100, height=50)


class FakeCapture:
    """Writes the next queued frame payload to a file per capture call."""

    def __init__(self, frames: list[bytes], errors_at: set[int] | None = None):
        self.frames = frames
        self.errors_at = errors_at or set()
        self.calls = 0

    def capture_rect(self, rect: Rect, *, output_dir: Path) -> CaptureResult:
        self.calls += 1
        if self.calls in self.errors_at:
            raise CaptureError(f"capture failed at call {self.calls}")
        index = min(self.calls - 1, len(self.frames) - 1)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"frame-{self.calls}.png"
        path.write_bytes(self.frames[index])
        return CaptureResult(rect=rect, image_path=path, backend="fake", elapsed_s=0.0)


class FakeOcr:
    """Maps frame payload bytes to OCR text."""

    def __init__(self, text_by_frame: dict[bytes, str]):
        self.text_by_frame = text_by_frame
        self.calls = 0

    def recognize(self, image_path: Path) -> OcrResult:
        self.calls += 1
        text = self.text_by_frame[image_path.read_bytes()]
        return OcrResult(
            text=text,
            lines=[text] if text else [],
            language="en-US",
            backend="fake-ocr",
            elapsed_s=0.0,
            image_width=100,
            image_height=50,
        )


class TranslateSpy:
    def __init__(self, errors_at: set[int] | None = None):
        self.calls: list[str] = []
        self.errors_at = errors_at or set()

    def __call__(self, text, capture_result, ocr_result, iteration):
        if len(self.calls) + 1 in self.errors_at:
            self.calls.append(f"error:{text}")
            raise BackendError("backend down")
        self.calls.append(text)
        return VisualTranslation(
            translated_text=f"KO:{text}",
            source_text=text,
            capture=capture_result,
        )


def _run_watcher(
    frames,
    texts,
    *,
    temp_dir,
    capture_errors=None,
    translate_errors=None,
    on_event=None,
    **config_kwargs,
):
    capture = FakeCapture(frames, errors_at=capture_errors)
    ocr = FakeOcr(texts)
    translate = TranslateSpy(errors_at=translate_errors)
    config = WatchConfig(rect=RECT, interval_s=0.0, **config_kwargs)
    watcher = RegionWatcher(
        capture=capture,
        ocr=ocr,
        translate=translate,
        config=config,
        output_dir=Path(temp_dir) / "captures",
        on_event=on_event,
        sleep=lambda _seconds: None,
    )
    with contextlib.redirect_stderr(io.StringIO()):
        summary = watcher.run()
    return summary, capture, ocr, translate


class RegionWatcherTest(unittest.TestCase):
    def test_unchanged_frames_translate_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, ocr, translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                max_iterations=3,
            )

        self.assertEqual(summary.iterations, 3)
        self.assertEqual(summary.changes, 1)
        self.assertEqual(summary.translations, 1)
        self.assertEqual(translate.calls, ["Hello"])
        self.assertEqual(ocr.calls, 1)

    def test_changed_frame_retranslates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, _ocr, translate = _run_watcher(
                [b"frame-a", b"frame-b"],
                {b"frame-a": "Hello", b"frame-b": "World"},
                temp_dir=temp_dir,
                max_iterations=2,
            )

        self.assertEqual(summary.changes, 2)
        self.assertEqual(summary.translations, 2)
        self.assertEqual(translate.calls, ["Hello", "World"])

    def test_same_ocr_text_skips_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, _ocr, translate = _run_watcher(
                [b"frame-a", b"frame-b"],
                {b"frame-a": "Hello", b"frame-b": "Hello"},
                temp_dir=temp_dir,
                max_iterations=2,
            )

        self.assertEqual(summary.changes, 2)
        self.assertEqual(summary.translations, 1)
        self.assertEqual(summary.skipped_same_text, 1)
        self.assertEqual(translate.calls, ["Hello"])

    def test_short_text_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, _ocr, translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": ""},
                temp_dir=temp_dir,
                max_iterations=1,
            )

        self.assertEqual(summary.skipped_short_text, 1)
        self.assertEqual(summary.translations, 0)
        self.assertEqual(translate.calls, [])

    def test_capture_error_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, _ocr, translate = _run_watcher(
                [b"frame-a", b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                capture_errors={1},
                max_iterations=2,
            )

        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.translations, 1)
        self.assertEqual(translate.calls, ["Hello"])

    def test_consecutive_errors_abort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            capture = FakeCapture([b"frame-a"], errors_at={1, 2})
            watcher = RegionWatcher(
                capture=capture,
                ocr=FakeOcr({b"frame-a": "Hello"}),
                translate=TranslateSpy(),
                config=WatchConfig(
                    rect=RECT,
                    interval_s=0.0,
                    max_iterations=10,
                    max_consecutive_errors=2,
                ),
                output_dir=Path(temp_dir) / "captures",
                sleep=lambda _seconds: None,
            )
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(WatchError):
                    watcher.run()

    def test_backend_error_retries_next_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, _capture, _ocr, translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                translate_errors={1},
                max_iterations=2,
            )

        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.translations, 1)
        self.assertEqual(summary.changes, 2)
        self.assertEqual(translate.calls, ["error:Hello", "Hello"])

    def test_captures_removed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _summary, _capture, _ocr, _translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                max_iterations=2,
            )
            leftovers = list((Path(temp_dir) / "captures").glob("*.png"))

        self.assertEqual(leftovers, [])

    def test_captures_kept_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _summary, _capture, _ocr, _translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                max_iterations=2,
                keep_captures=True,
            )
            leftovers = list((Path(temp_dir) / "captures").glob("*.png"))

        self.assertEqual(len(leftovers), 2)

    def test_stability_waits_for_settled_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, capture, _ocr, translate = _run_watcher(
                [b"frame-a", b"frame-b", b"frame-b", b"frame-b"],
                {b"frame-a": "Hello", b"frame-b": "World"},
                temp_dir=temp_dir,
                max_iterations=2,
                stability_checks=1,
            )

        # Iteration 1 captures frame-a, stability recapture sees frame-b,
        # then confirms frame-b; iteration 2 sees frame-b unchanged.
        self.assertEqual(translate.calls, ["World"])
        self.assertEqual(summary.translations, 1)
        self.assertEqual(capture.calls, 4)

    def test_stability_gives_up_after_max_attempts(self) -> None:
        # checks=1 -> max_attempts = 1*3 + 3 = 6; seven distinct payloads keep
        # the region churning for the whole debounce window.
        frames = [b"f1", b"f2", b"f3", b"f4", b"f5", b"f6", b"f7"]
        texts = {frame: f"Text {frame.decode()}" for frame in frames}
        with tempfile.TemporaryDirectory() as temp_dir:
            summary, capture, _ocr, translate = _run_watcher(
                frames,
                texts,
                temp_dir=temp_dir,
                max_iterations=1,
                stability_checks=1,
            )
            leftovers = list((Path(temp_dir) / "captures").glob("*.png"))

        self.assertEqual(capture.calls, 7)  # 1 initial + max_attempts recaptures
        self.assertEqual(translate.calls, ["Text f7"])  # latest frame processed
        self.assertEqual(summary.translations, 1)
        self.assertEqual(leftovers, [])  # every replaced frame was cleaned up

    def test_no_capture_leak_when_ocr_fails(self) -> None:
        class FailingOcr:
            def recognize(self, image_path):
                raise OcrError("ocr broke")

        with tempfile.TemporaryDirectory() as temp_dir:
            capture = FakeCapture([b"frame-a"])
            watcher = RegionWatcher(
                capture=capture,
                ocr=FailingOcr(),
                translate=TranslateSpy(),
                config=WatchConfig(rect=RECT, interval_s=0.0, max_iterations=1),
                output_dir=Path(temp_dir) / "captures",
                sleep=lambda _seconds: None,
            )
            with contextlib.redirect_stderr(io.StringIO()):
                summary = watcher.run()
            leftovers = list((Path(temp_dir) / "captures").glob("*.png"))

        self.assertEqual(summary.errors, 1)
        self.assertEqual(leftovers, [])

    def test_config_rejects_zero_min_text_chars(self) -> None:
        with self.assertRaises(ValueError):
            WatchConfig(rect=RECT, min_text_chars=0)

    def test_on_event_receives_translation(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            _summary, _capture, _ocr, _translate = _run_watcher(
                [b"frame-a"],
                {b"frame-a": "Hello"},
                temp_dir=temp_dir,
                max_iterations=1,
                on_event=events.append,
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].translation.translated_text, "KO:Hello")
        self.assertEqual(events[0].iteration, 1)


if __name__ == "__main__":
    unittest.main()
