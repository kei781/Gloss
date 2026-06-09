from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from gloss.log import log
from gloss.metrics import new_request_id
from gloss.visual.models import CaptureResult, Rect


class CaptureError(RuntimeError):
    pass


class PowerShellScreenCapture:
    def __init__(self, *, script_path: Path | None = None):
        self.script_path = script_path or Path("scripts/phase2/capture_screen_rect.ps1")

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
