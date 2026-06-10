from __future__ import annotations

import argparse
from pathlib import Path
import sys

from gloss.backend.openai_client import BackendError, OpenAIChatClient
from gloss.config import load_runtime_config
from gloss.log import log
from gloss.metrics import MetricsRecorder, now_iso
from gloss.overlay.tk_overlay import OverlayController, OverlayError, OverlayGeometry
from gloss.visual.capture import CaptureError, PowerShellScreenCapture
from gloss.visual.engine import VisualEngine, VisualEngineError
from gloss.visual.models import CaptureResult, Rect
from gloss.visual.ocr import OcrError, OcrResult, WindowsOcr, ocr_metrics
from gloss.visual.watch import (
    RegionWatcher,
    WatchConfig,
    WatchError,
    WatchEvent,
    WatchSummary,
)


DEFAULT_PHASE3_METRICS = Path("runs/phase3/watch-metrics.jsonl")
DEFAULT_PHASE3_CAPTURES = Path("runs/phase3/captures")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Gloss Phase 3 region watch: capture a screen rect on an "
            "interval, re-translate only when the content changes. Intended "
            "for slow transitions (slides, static subtitles)."
        )
    )
    parser.add_argument(
        "--watch-rect",
        required=True,
        help="Screen rect to watch as X,Y,WIDTH,HEIGHT.",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between captures.")
    parser.add_argument(
        "--stability",
        type=int,
        default=0,
        help="Consecutive identical frames required before processing a change.",
    )
    parser.add_argument("--min-text-chars", type=int, default=2)
    parser.add_argument(
        "--max-iterations",
        type=int,
        help="Stop after N capture iterations (default: run until Ctrl+C).",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=5,
        help="Abort after this many consecutive iteration errors.",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=["windows"],
        default="windows",
        help="OCR helper used on changed frames (CPU helper; ADR-013/ADR-016).",
    )
    parser.add_argument("--ocr-language", help="OCR language tag, e.g. ko, en-US, ja.")
    parser.add_argument(
        "--capture-output",
        type=Path,
        default=DEFAULT_PHASE3_CAPTURES,
        help="Directory for capture images.",
    )
    parser.add_argument(
        "--keep-captures",
        action="store_true",
        help="Keep capture PNGs instead of deleting them after processing.",
    )
    parser.add_argument("--config", type=Path, help="Config JSON path.")
    parser.add_argument("--env-file", type=Path, help="Env file path.")
    parser.add_argument("--profile", help="Model profile override.")
    parser.add_argument("--model", help="Runtime model override.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL override.")
    parser.add_argument("--api-key", help="API key override.")
    parser.add_argument("--metrics", type=Path, help="Metrics JSONL output path.")
    parser.add_argument("--max-tokens", type=int, help="Max generated tokens per request.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature.")
    parser.add_argument("--timeout", type=float, help="Backend timeout seconds.")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run capture/diff/OCR but skip the backend call.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Append timestamped translations to this file.",
    )
    parser.add_argument("--overlay", action="store_true", help="Show output in overlay.")
    parser.add_argument("--overlay-rect", default="80,720,1000,180")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        rect = Rect.parse(args.watch_rect)
        overlay_geometry = (
            OverlayGeometry.parse(args.overlay_rect) if args.overlay else None
        )
        config = load_runtime_config(
            config_path=args.config,
            env_file=args.env_file,
            profile=args.profile,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            metrics_path=args.metrics or DEFAULT_PHASE3_METRICS,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout_s=args.timeout,
        )
        if config.env_file:
            log("loaded env file", path=str(config.env_file))
        if config.config_path:
            log("loaded config", path=str(config.config_path))

        client = OpenAIChatClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout_s=config.timeout_s,
        )
        engine = VisualEngine(
            config=config,
            client=client,
            metrics=MetricsRecorder(config.metrics_path),
            dry_run=args.dry_run,
        )
        if args.ocr_backend == "windows":
            ocr = WindowsOcr(language=args.ocr_language)
        else:  # unreachable while choices=["windows"]; guards future backends
            raise ValueError(f"Unsupported --ocr-backend: {args.ocr_backend}")
        watch_config = WatchConfig(
            rect=rect,
            interval_s=args.interval,
            stability_checks=args.stability,
            min_text_chars=args.min_text_chars,
            max_iterations=args.max_iterations,
            max_consecutive_errors=args.max_consecutive_errors,
            keep_captures=args.keep_captures,
        )

        def translate(
            text: str,
            capture_result: CaptureResult,
            ocr_result: OcrResult,
            iteration: int,
        ):
            return engine.translate_ocr_text(
                text,
                capture=capture_result,
                stream=not args.no_stream,
                input_mode="watch_ocr",
                phase=3,
                metrics_extra={
                    "watch": {
                        "iteration": iteration,
                        "interval_s": args.interval,
                        "stability_checks": args.stability,
                    },
                    "ocr": ocr_metrics(ocr_result),
                },
            )

        watcher = RegionWatcher(
            capture=PowerShellScreenCapture(),
            ocr=ocr,
            translate=translate,
            config=watch_config,
            output_dir=args.capture_output,
            on_event=lambda event: _handle_event(event, args),
        )
    except (ValueError, OverlayError) as exc:
        log(str(exc), level="ERROR")
        return 1

    try:
        if overlay_geometry is not None:
            summary = _run_with_overlay(watcher, overlay_geometry)
        else:
            summary = watcher.run()
    except KeyboardInterrupt:
        log("watch stopped by user")
        return 0
    except (
        BackendError,
        CaptureError,
        OcrError,
        OverlayError,
        VisualEngineError,
        WatchError,
        OSError,
    ) as exc:
        log(str(exc), level="ERROR")
        return 1

    _log_summary(summary)
    return 0


def _run_with_overlay(watcher: RegionWatcher, geometry: OverlayGeometry) -> WatchSummary:
    controller = OverlayController(geometry=geometry)

    outcome: dict[str, object] = {}
    base_on_event = watcher.on_event

    def on_event(event: WatchEvent) -> None:
        if base_on_event is not None:
            base_on_event(event)
        controller.show(event.translation.translated_text)

    watcher.on_event = on_event

    def worker() -> None:
        try:
            outcome["summary"] = watcher.run()
        except BaseException as exc:  # surfaced after the overlay closes
            outcome["error"] = exc
        finally:
            controller.close()

    controller.run(worker)

    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    summary = outcome.get("summary")
    if isinstance(summary, WatchSummary):
        return summary
    return WatchSummary()


def _handle_event(event: WatchEvent, args: argparse.Namespace) -> None:
    text = event.translation.translated_text.strip()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8") as handle:
            handle.write(f"[{now_iso()}] (iteration {event.iteration})\n{text}\n\n")
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def _log_summary(summary: WatchSummary) -> None:
    log(
        "watch summary",
        iterations=summary.iterations,
        changes=summary.changes,
        ocr_runs=summary.ocr_runs,
        translations=summary.translations,
        skipped_same_text=summary.skipped_same_text,
        skipped_short_text=summary.skipped_short_text,
        errors=summary.errors,
    )


if __name__ == "__main__":
    raise SystemExit(main())
