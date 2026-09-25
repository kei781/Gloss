"""Gloss hover daemon (FR-V2): global hotkey -> capture around cursor ->
Windows OCR (CPU helper) -> NPU translation -> subtitle overlay.

This interactive front supports both OCR + text LLM and direct VLM input.
Tk must
own the main thread on Windows, so the overlay runs the Tk mainloop while a
daemon worker polls the hotkey and drives the capture/OCR/translate pipeline.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import threading
import time

from gloss.backend.openai_client import BackendError, OpenAIChatClient
from gloss.config import load_runtime_config
from gloss.log import log
from gloss.metrics import MetricsRecorder
from gloss.overlay.interactive_overlay import InteractiveOverlay
from gloss.overlay.tk_overlay import OverlayError, OverlayGeometry
from gloss.visual.capture import CaptureError, make_screen_capture
from gloss.visual.display import default_overlay_geometry, enable_dpi_awareness
from gloss.visual.engine import VisualEngine, VisualEngineError
from gloss.visual.models import Rect
from gloss.visual.ocr import OcrError, WindowsOcr, ocr_metrics


DEFAULT_PHASE2_METRICS = Path("runs/phase2/visual-metrics.jsonl")

# Virtual-key codes for modifier/letter hotkey parsing.
_MODIFIERS = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
}
_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


class HotkeySpec:
    """A hotkey parsed into the set of virtual-key codes that must be down."""

    def __init__(self, label: str, vks: list[int]):
        self.label = label
        self.vks = vks

    @classmethod
    def parse(cls, value: str) -> "HotkeySpec":
        tokens = [t.strip().lower() for t in value.split("+") if t.strip()]
        if not tokens:
            raise ValueError("Hotkey must not be empty (e.g. ctrl+alt+z).")
        vks: list[int] = []
        for token in tokens:
            if token in _MODIFIERS:
                vks.append(_MODIFIERS[token])
            elif token in _NAMED_KEYS:
                vks.append(_NAMED_KEYS[token])
            elif len(token) == 1 and token.isalnum():
                vks.append(ord(token.upper()))
            else:
                raise ValueError(f"Unsupported hotkey token: {token!r}")
        return cls(label="+".join(tokens), vks=vks)


def _enable_dpi_awareness() -> None:
    enable_dpi_awareness()


def _key_down(vk: int) -> bool:
    # GetAsyncKeyState high bit (0x8000) set => key currently down.
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _all_down(vks: list[int]) -> bool:
    return all(_key_down(vk) for vk in vks)


def _cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def _virtual_screen() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
    x = user32.GetSystemMetrics(76)
    y = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    return x, y, w, h


def _primary_screen() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # SM_CXSCREEN, SM_CYSCREEN


def _region_around_cursor(width: int, height: int) -> Rect:
    cx, cy = _cursor_pos()
    vx, vy, vw, vh = _virtual_screen()
    x = cx - width // 2
    y = cy - height // 2
    # Clamp so the rect stays fully inside the virtual desktop.
    x = max(vx, min(x, vx + vw - width))
    y = max(vy, min(y, vy + vh - height))
    return Rect(x=x, y=y, width=width, height=height)


def _default_overlay_geometry() -> OverlayGeometry:
    return default_overlay_geometry()


def _cleanup_capture_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log(
            "failed to remove hover capture file",
            level="WARN",
            path=str(path),
            error=str(exc),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gloss hover daemon (FR-V2): press a global hotkey to translate the "
            "screen text around the mouse cursor and show it as a subtitle overlay."
        )
    )
    parser.add_argument("--hotkey", default="ctrl+alt+z", help="Translate hotkey, e.g. ctrl+alt+z.")
    parser.add_argument("--quit-hotkey", default="ctrl+alt+q", help="Quit hotkey.")
    parser.add_argument(
        "--lock-hotkey",
        default="ctrl+alt+l",
        help="Toggle the overlay lock (lock = click-through; unlock needs this hotkey).",
    )
    parser.add_argument(
        "--region",
        default="640,260",
        help="Capture box around the cursor as WIDTH,HEIGHT.",
    )
    parser.add_argument(
        "--overlay-rect",
        help="Subtitle box as X,Y,WIDTH,HEIGHT. Default: bottom-center band.",
    )
    parser.add_argument("--overlay-opacity", type=float, default=0.88)
    parser.add_argument("--ocr-language", default="en-US", help="OCR language tag, e.g. en-US, ja, ko.")
    parser.add_argument(
        "--input-mode", choices=["ocr", "vlm"], default="ocr",
        help="Use Windows OCR plus a text model, or send the captured image to a VLM.",
    )
    parser.add_argument(
        "--capture-backend", choices=["auto", "wgc", "dxgi", "gdi"], default="auto",
        help="Capture with WGC, DXGI, or GDI; auto tries them in that order.",
    )
    parser.add_argument(
        "--vlm-max-edge", type=int, default=1024,
        help="Resize the longest captured image edge before VLM inference (256-2048).",
    )
    parser.add_argument("--poll-ms", type=int, default=60, help="Hotkey poll interval (ms).")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Must run before any GetCursorPos / Tk window so all coordinates are physical
    # pixels, matching the DPI-aware capture helper.
    _enable_dpi_awareness()

    try:
        translate_key = HotkeySpec.parse(args.hotkey)
        quit_key = HotkeySpec.parse(args.quit_hotkey)
        lock_key = HotkeySpec.parse(args.lock_hotkey)
        region_parts = [p.strip() for p in args.region.split(",")]
        if len(region_parts) != 2:
            raise ValueError("--region must be WIDTH,HEIGHT.")
        region_w, region_h = (int(region_parts[0]), int(region_parts[1]))
        if region_w <= 0 or region_h <= 0:
            raise ValueError("--region width and height must be > 0.")
        if args.input_mode == "vlm" and not 256 <= args.vlm_max_edge <= 2048:
            raise ValueError("--vlm-max-edge must be between 256 and 2048.")

        geometry = (
            OverlayGeometry.parse(args.overlay_rect)
            if args.overlay_rect
            else _default_overlay_geometry()
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
    except ValueError as exc:
        log(str(exc), level="ERROR")
        return 1

    if config.env_file:
        log("loaded env file", path=str(config.env_file))
    log(
        "hover daemon ready",
        hotkey=translate_key.label,
        quit=quit_key.label,
        model=config.model,
        base_url=config.base_url,
        region=f"{region_w}x{region_h}",
    )

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
        dry_run=False,
    )
    try:
        capturer = make_screen_capture(args.capture_backend)
    except CaptureError as exc:
        log(str(exc), level="ERROR")
        return 1
    ocr = WindowsOcr(language=args.ocr_language) if args.input_mode == "ocr" else None
    capture_dir = Path("runs/phase2/captures")

    controller = InteractiveOverlay(
        geometry=geometry,
        opacity=args.overlay_opacity,
        unlock_hint=lock_key.label,
        initial_text=(
            f"Gloss 준비됨 — {translate_key.label} 번역 / {lock_key.label} 잠금 / {quit_key.label} 종료  "
            "(가장자리=크기, 가운데=이동, 우상단=투명도·잠금)"
        ),
    )

    def handle_trigger() -> None:
        controller.show("● 캡처 중...")
        rect = _region_around_cursor(region_w, region_h)
        capture = None
        try:
            try:
                capture = capturer.capture_rect(rect, output_dir=capture_dir)
            except (CaptureError, OSError) as exc:
                controller.show(f"[캡처 실패] {exc}")
                return
            if args.input_mode == "vlm":
                controller.show("● 이미지 번역 중 (VLM)...")
                translate = lambda: engine.translate_image(
                    capture.image_path, capture=capture, stream=not args.no_stream,
                    max_image_edge=args.vlm_max_edge,
                )
            else:
                controller.show("● 글자 읽는 중 (OCR)...")
                try:
                    ocr_result = ocr.recognize(capture.image_path)
                except (OcrError, OSError) as exc:
                    controller.show(f"[OCR 실패] {exc}")
                    return
                source_text = ocr_result.text.strip()
                if not source_text:
                    controller.show("(이 영역에서 글자를 찾지 못했어요)")
                    return
                controller.show("● 번역 중 (NPU)...")
                translate = lambda: engine.translate_ocr_text(
                    source_text, capture=capture, stream=not args.no_stream,
                    input_mode="windows_ocr", metrics_extra={"ocr": ocr_metrics(ocr_result)},
                )
            try:
                translated = translate()
            except (BackendError, VisualEngineError, OSError) as exc:
                controller.show(f"[번역 실패] {exc}")
                return
            controller.show(translated.translated_text.strip() or "(번역 결과 없음)")
        finally:
            if capture is not None:
                _cleanup_capture_file(capture.image_path)

    stop = threading.Event()

    def worker() -> None:
        poll_s = max(0.01, args.poll_ms / 1000.0)
        armed = True       # translate-key debounce
        lock_armed = True  # lock-key debounce
        while not stop.is_set():
            try:
                if _all_down(quit_key.vks):
                    log("hover daemon quit by hotkey", hotkey=quit_key.label)
                    controller.close()
                    return
                if lock_armed and _all_down(lock_key.vks):
                    lock_armed = False
                    controller.toggle_lock()
                elif not _all_down(lock_key.vks):
                    lock_armed = True
                if armed and _all_down(translate_key.vks):
                    armed = False
                    handle_trigger()
                elif not _all_down(translate_key.vks):
                    armed = True
            except Exception as exc:  # never let the worker thread die silently
                log("hover trigger error", level="ERROR", error=str(exc))
                armed = True
                lock_armed = True
            time.sleep(poll_s)

    try:
        controller.run(worker)
    except OverlayError as exc:
        log(str(exc), level="ERROR")
        return 1
    except KeyboardInterrupt:
        log("hover daemon stopped by user")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
