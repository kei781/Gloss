from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from typing import Callable, Protocol

from gloss.backend.openai_client import BackendError
from gloss.log import log
from gloss.visual.capture import CaptureError
from gloss.visual.models import CaptureResult, Rect, VisualTranslation
from gloss.visual.ocr import OcrError, OcrResult


class WatchError(RuntimeError):
    pass


class CaptureBackend(Protocol):
    def capture_rect(self, rect: Rect, *, output_dir: Path) -> CaptureResult: ...


class OcrBackend(Protocol):
    def recognize(self, image_path: Path) -> OcrResult: ...


Translate = Callable[[str, CaptureResult, OcrResult, int], VisualTranslation]


@dataclass(frozen=True)
class WatchConfig:
    rect: Rect
    interval_s: float = 2.0
    stability_checks: int = 0
    min_text_chars: int = 2
    max_iterations: int | None = None
    max_consecutive_errors: int = 5
    keep_captures: bool = False

    def __post_init__(self) -> None:
        if self.min_text_chars < 1:
            raise ValueError("min_text_chars must be >= 1")
        if self.interval_s < 0:
            raise ValueError("interval_s must be >= 0")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")


@dataclass
class WatchSummary:
    iterations: int = 0
    changes: int = 0
    ocr_runs: int = 0
    translations: int = 0
    skipped_same_text: int = 0
    skipped_short_text: int = 0
    errors: int = 0


@dataclass(frozen=True)
class WatchEvent:
    iteration: int
    ocr_text: str
    translation: VisualTranslation
    capture: CaptureResult
    ocr: OcrResult


class RegionWatcher:
    """FR-V3 region watch: periodic capture, frame diff, retranslate on change.

    Designed for slow transitions (slides, static subtitles). The frame diff
    is a content hash of the capture file, so any pixel change in the rect
    counts as a change; OCR-text equality then gates actual retranslation.
    """

    def __init__(
        self,
        *,
        capture: CaptureBackend,
        ocr: OcrBackend,
        translate: Translate,
        config: WatchConfig,
        output_dir: Path,
        on_event: Callable[[WatchEvent], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.capture = capture
        self.ocr = ocr
        self.translate = translate
        self.config = config
        self.output_dir = output_dir
        self.on_event = on_event
        self.sleep = sleep
        self._last_hash: str | None = None
        self._last_text: str | None = None

    def run(self) -> WatchSummary:
        summary = WatchSummary()
        consecutive_errors = 0
        iteration = 0

        log(
            "region watch started",
            x=self.config.rect.x,
            y=self.config.rect.y,
            width=self.config.rect.width,
            height=self.config.rect.height,
            interval_s=self.config.interval_s,
            max_iterations=self.config.max_iterations,
        )
        while self.config.max_iterations is None or iteration < self.config.max_iterations:
            iteration += 1
            summary.iterations = iteration
            try:
                self._run_iteration(iteration, summary)
                consecutive_errors = 0
            except (BackendError, CaptureError, OcrError, OSError) as exc:
                summary.errors += 1
                consecutive_errors += 1
                log(
                    "watch iteration failed",
                    level="ERROR",
                    iteration=iteration,
                    error=str(exc),
                )
                if consecutive_errors >= self.config.max_consecutive_errors:
                    raise WatchError(
                        f"Aborting watch after {consecutive_errors} consecutive errors."
                    ) from exc

            if self.config.max_iterations is not None and iteration >= self.config.max_iterations:
                break
            self.sleep(self.config.interval_s)

        log(
            "region watch finished",
            iterations=summary.iterations,
            changes=summary.changes,
            translations=summary.translations,
            errors=summary.errors,
        )
        return summary

    def _run_iteration(self, iteration: int, summary: WatchSummary) -> None:
        capture_result = self.capture.capture_rect(
            self.config.rect, output_dir=self.output_dir
        )
        # The finally below re-reads capture_result, so it also covers the
        # frame swapped in by _wait_until_stable; double-unlink is harmless
        # because _cleanup uses missing_ok.
        try:
            frame_hash = _hash_file(capture_result.image_path)
            if frame_hash == self._last_hash:
                return

            capture_result, frame_hash = self._wait_until_stable(
                capture_result, frame_hash
            )
            summary.changes += 1
            log("watch change detected", iteration=iteration, frame=frame_hash[:12])

            ocr_result = self.ocr.recognize(capture_result.image_path)
            summary.ocr_runs += 1
            text = ocr_result.text.strip()

            if len(text) < self.config.min_text_chars:
                summary.skipped_short_text += 1
                self._last_hash = frame_hash
                self._last_text = text
                log("watch skipped short text", iteration=iteration, chars=len(text))
                return

            if self._last_text is not None and text == self._last_text:
                summary.skipped_same_text += 1
                self._last_hash = frame_hash
                log("watch skipped unchanged text", iteration=iteration)
                return

            translation = self.translate(text, capture_result, ocr_result, iteration)
            summary.translations += 1
            self._last_hash = frame_hash
            self._last_text = text
            if self.on_event is not None:
                self.on_event(
                    WatchEvent(
                        iteration=iteration,
                        ocr_text=text,
                        translation=translation,
                        capture=capture_result,
                        ocr=ocr_result,
                    )
                )
        finally:
            self._cleanup(capture_result)

    def _wait_until_stable(
        self, capture_result: CaptureResult, frame_hash: str
    ) -> tuple[CaptureResult, str]:
        """Debounce mid-transition frames by requiring consecutive equal hashes."""
        checks = self.config.stability_checks
        if checks <= 0:
            return capture_result, frame_hash

        matches = 0
        attempts = 0
        max_attempts = checks * 3 + 3
        while matches < checks and attempts < max_attempts:
            attempts += 1
            self.sleep(self.config.interval_s)
            try:
                next_capture = self.capture.capture_rect(
                    self.config.rect, output_dir=self.output_dir
                )
            except Exception:
                self._cleanup(capture_result)
                raise
            try:
                next_hash = _hash_file(next_capture.image_path)
            except OSError:
                self._cleanup(next_capture)
                self._cleanup(capture_result)
                raise
            if next_hash == frame_hash:
                matches += 1
                self._cleanup(next_capture)
                continue
            self._cleanup(capture_result)
            capture_result, frame_hash = next_capture, next_hash
            matches = 0
        if matches < checks:
            log(
                "watch frame never stabilized; processing latest frame",
                level="WARN",
                attempts=attempts,
            )
        return capture_result, frame_hash

    def _cleanup(self, capture_result: CaptureResult) -> None:
        if self.config.keep_captures:
            return
        try:
            capture_result.image_path.unlink(missing_ok=True)
        except OSError as exc:
            log(
                "failed to remove capture file",
                level="WARN",
                path=str(capture_result.image_path),
                error=str(exc),
            )


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
