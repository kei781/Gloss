from __future__ import annotations

from importlib import resources
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import time

from gloss.log import log
from gloss.metrics import new_request_id
from gloss.visual.models import CaptureResult, Rect


DEFAULT_CAPTURE_SCRIPT = Path(
    str(
        resources.files("gloss.visual")
        .joinpath("resources")
        .joinpath("capture_screen_rect.ps1")
    )
)


class CaptureError(RuntimeError):
    pass


def make_screen_capture(backend: str = "auto"):
    """Prefer Windows Graphics Capture when its optional dependencies exist."""
    if backend not in {"auto", "wgc", "dxgi", "gdi"}:
        raise ValueError(f"Unsupported capture backend: {backend}")
    if backend == "gdi":
        return PowerShellScreenCapture()
    if find_spec("dxcam") is None or find_spec("PIL") is None:
        if backend in {"wgc", "dxgi"}:
            raise CaptureError("GPU capture requires: pip install -e '.[capture]'")
        log("GPU capture dependencies unavailable; using GDI capture", level="WARN")
        return PowerShellScreenCapture()
    if backend == "wgc":
        return WindowsGraphicsCapture()
    if backend == "dxgi":
        return WindowsGraphicsCapture(backend="dxgi")
    return FallbackScreenCapture(
        WindowsGraphicsCapture(),
        FallbackScreenCapture(
            WindowsGraphicsCapture(backend="dxgi"), PowerShellScreenCapture(), label="DXGI"
        ),
        label="WGC",
    )


class FallbackScreenCapture:
    def __init__(self, primary, fallback, *, label: str = "primary"):
        self.primary = primary
        self.fallback = fallback
        self.label = label

    def capture_rect(self, rect: Rect, *, output_dir: Path) -> CaptureResult:
        try:
            return self.primary.capture_rect(rect, output_dir=output_dir)
        except CaptureError as exc:
            log(f"{self.label} capture failed; trying fallback", level="WARN", error=str(exc))
            return self.fallback.capture_rect(rect, output_dir=output_dir)


class WindowsGraphicsCapture:
    """One-shot Windows.Graphics.Capture or DXGI via DXcam."""

    def __init__(self, *, backend: str = "winrt"):
        self.backend = backend

    def capture_rect(self, rect: Rect, *, output_dir: Path) -> CaptureResult:
        try:
            import dxcam
            from PIL import Image
        except (ImportError, OSError) as exc:
            raise CaptureError("WGC capture requires: pip install -e '.[capture]'") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"capture-{new_request_id()}.png"
        region = (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)
        started_at = time.perf_counter()
        try:
            with dxcam.create(backend=self.backend, output_color="BGRA", processor_backend="numpy") as camera:
                frame = camera.grab(region=region, new_frame_only=False)
                if frame is None:
                    time.sleep(0.05)
                    frame = camera.grab(region=region, new_frame_only=False)
            if frame is None:
                raise CaptureError(f"{self.backend} returned no frame for the requested region.")
            height, width = frame.shape[:2]
            if (width, height) != (rect.width, rect.height):
                raise CaptureError(f"{self.backend} capture size does not match the requested region.")
            Image.frombytes(
                "RGBA", (width, height), frame.tobytes(), "raw", "BGRA"
            ).convert("RGB").save(image_path, format="PNG")
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(f"{self.backend} capture failed: {exc}") from exc
        elapsed_s = max(time.perf_counter() - started_at, 0.0)
        backend_name = "wgc-winrt" if self.backend == "winrt" else "dxgi-duplication"
        log("visual capture completed", backend=backend_name, path=str(image_path), elapsed_s=elapsed_s)
        return CaptureResult(
            rect=rect, image_path=image_path, backend=backend_name, elapsed_s=elapsed_s,
        )


class PowerShellScreenCapture:
    def __init__(self, *, script_path: Path | None = None):
        self.script_path = script_path or DEFAULT_CAPTURE_SCRIPT

    def capture_rect(self, rect: Rect, *, output_dir: Path) -> CaptureResult:
        if not self.script_path.exists():
            raise CaptureError(f"Capture script not found: {self.script_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"capture-{new_request_id()}.png"

        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-X",
            str(rect.x),
            "-Y",
            str(rect.y),
            "-Width",
            str(rect.width),
            "-Height",
            str(rect.height),
            "-Output",
            str(image_path),
        ]
        started_at = time.perf_counter()
        log(
            "visual capture started",
            backend="gdi-copy-from-screen",
            x=rect.x,
            y=rect.y,
            width=rect.width,
            height=rect.height,
        )
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        elapsed_s = max(time.perf_counter() - started_at, 0.0)
        if completed.returncode != 0:
            error_text = (completed.stderr or completed.stdout or "").strip()
            error_text = error_text.removeprefix("[ERROR]").strip()
            raise CaptureError(error_text or "Screen capture failed.")

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"Capture script returned invalid JSON: {completed.stdout}") from exc

        captured_path = Path(payload.get("output") or image_path)
        if not captured_path.exists():
            raise CaptureError(f"Capture output not found: {captured_path}")

        log("visual capture completed", path=str(captured_path), elapsed_s=elapsed_s)
        return CaptureResult(
            rect=rect,
            image_path=captured_path,
            backend=str(payload.get("backend") or "gdi-copy-from-screen"),
            elapsed_s=elapsed_s,
        )
