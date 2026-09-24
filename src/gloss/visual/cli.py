from __future__ import annotations

import argparse
from pathlib import Path
import sys

from gloss.backend.openai_client import BackendError, OpenAIChatClient
from gloss.config import load_runtime_config
from gloss.log import log
from gloss.metrics import MetricsRecorder
from gloss.overlay.tk_overlay import OverlayError, OverlayGeometry, show_overlay_text
from gloss.visual.capture import CaptureError, make_screen_capture
from gloss.visual.engine import VisualEngine, VisualEngineError
from gloss.visual.models import CaptureResult, Rect
from gloss.visual.ocr import OcrError, WindowsOcr, ocr_metrics


DEFAULT_PHASE2_METRICS = Path("runs/phase2/visual-metrics.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Gloss Phase 2 visual engine.")
    parser.add_argument("--capture-rect", help="Capture screen rect as X,Y,WIDTH,HEIGHT.")
    parser.add_argument(
        "--capture-backend", choices=["auto", "wgc", "dxgi", "gdi"], default="auto",
        help="Capture with WGC, DXGI, or GDI; auto tries them in that order.",
    )
    parser.add_argument(
        "--capture-output",
        type=Path,
        default=Path("runs/phase2/captures"),
        help="Directory for captured screen images.",
    )
    ocr = parser.add_mutually_exclusive_group()
    ocr.add_argument("--ocr-text", help="Text already extracted from the captured region.")
    ocr.add_argument("--ocr-file", type=Path, help="UTF-8 text file with OCR output.")
    ocr.add_argument(
        "--ocr-backend",
        choices=["windows"],
        help="Run OCR on the captured image (requires --capture-rect).",
    )
    ocr.add_argument(
        "--image-file", type=Path,
        help="Send a PNG/JPEG image to a VLM backend instead of using OCR.",
    )
    ocr.add_argument(
        "--vlm", action="store_true",
        help="Capture --capture-rect and send the image directly to a VLM backend.",
    )
    parser.add_argument(
        "--vlm-max-edge", type=int, default=1024,
        help="Resize the longest image edge before VLM inference (256-2048; default 1024).",
    )
    parser.add_argument("--ocr-language", help="OCR language tag, e.g. ko, en-US, ja.")
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
    parser.add_argument("--dry-run", action="store_true", help="Skip backend call.")
    parser.add_argument("--output", type=Path, help="Write translated text to this file.")
    parser.add_argument("--overlay", action="store_true", help="Show output in overlay.")
    parser.add_argument("--overlay-rect", default="80,720,1000,180")
    parser.add_argument("--overlay-duration", type=float, default=6.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        source_text = read_ocr_text(args)
        if args.image_file and args.capture_rect:
            raise VisualEngineError("Use --image-file or --capture-rect, not both.")
        if (args.ocr_backend or args.vlm) and not args.capture_rect:
            raise VisualEngineError("--ocr-backend and --vlm require --capture-rect.")
        if source_text is None and not args.ocr_backend and not args.vlm and not args.image_file and not args.dry_run:
            raise VisualEngineError(
                "Provide --ocr-text, --ocr-file, --ocr-backend windows, --image-file, or --vlm."
            )
        capture = capture_if_requested(args)
        ocr_result = None
        if source_text is None and args.ocr_backend == "windows" and capture is not None:
            ocr_result = WindowsOcr(language=args.ocr_language).recognize(
                capture.image_path
            )
            source_text = ocr_result.text.strip()
            if not source_text:
                raise VisualEngineError(
                    "Windows OCR found no text in the captured region."
                )
        if source_text is None and args.dry_run and capture is not None and not args.vlm:
            source_text = f"Captured screen region: {capture.image_path}"
        if source_text is None and not args.vlm and not args.image_file:
            raise VisualEngineError(
                "Provide --ocr-text, --ocr-file, --ocr-backend windows, --image-file, or --vlm."
            )

        config = load_runtime_config(
            config_path=args.config,
            env_file=args.env_file,
            profile=args.profile,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            metrics_path=args.metrics or DEFAULT_PHASE2_METRICS,
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
        if args.image_file or args.vlm:
            image_path = args.image_file or capture.image_path
            translated = engine.translate_image(
                image_path, capture=capture, stream=not args.no_stream,
                max_image_edge=args.vlm_max_edge,
            )
        else:
            translated = engine.translate_ocr_text(
                source_text,
                capture=capture,
                stream=not args.no_stream,
                input_mode="windows_ocr" if ocr_result is not None else "ocr_text",
                metrics_extra={"ocr": ocr_metrics(ocr_result)} if ocr_result else None,
            )
    except (
        BackendError,
        CaptureError,
        OcrError,
        OverlayError,
        ValueError,
        VisualEngineError,
        OSError,
    ) as exc:
        log(str(exc), level="ERROR")
        return 1

    output_text = translated.translated_text.strip() + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
        log("wrote visual translation output", path=str(args.output))
    else:
        sys.stdout.write(output_text)

    if args.overlay:
        geometry = OverlayGeometry.parse(args.overlay_rect)
        show_overlay_text(
            output_text.strip(),
            geometry=geometry,
            duration_s=args.overlay_duration,
        )
    return 0


def capture_if_requested(args: argparse.Namespace) -> CaptureResult | None:
    if not args.capture_rect:
        return None
    rect = Rect.parse(args.capture_rect)
    return make_screen_capture(args.capture_backend).capture_rect(
        rect, output_dir=args.capture_output
    )


def read_ocr_text(args: argparse.Namespace) -> str | None:
    if args.ocr_text is not None:
        return args.ocr_text
    if args.ocr_file is not None:
        try:
            return args.ocr_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise VisualEngineError(f"OCR file must be UTF-8: {args.ocr_file}") from exc
    return None


if __name__ == "__main__":
    raise SystemExit(main())
