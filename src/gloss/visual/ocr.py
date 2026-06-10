from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from gloss.log import log


# Anchored to the repo root (src/gloss/visual/ -> repo) so gloss-watch works
# from any cwd; the capture helper path is still cwd-relative (pre-existing).
DEFAULT_OCR_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "phase3" / "ocr_image_text.ps1"
)
SPACELESS_LANGUAGE_PREFIXES = ("ja", "zh")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class OcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrResult:
    text: str
    lines: list[str]
    language: str | None
    backend: str
    elapsed_s: float
    image_width: int | None
    image_height: int | None


class WindowsOcr:
    """Windows.Media.Ocr wrapper via scripts/phase3/ocr_image_text.ps1.

    The OCR runs on CPU and is the sanctioned lightweight helper from
    ADR-013/NFR-1; LLM translation stays on the NPU backend.
    """

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        language: str | None = None,
        timeout_s: float = 60.0,
        run: Runner = subprocess.run,
    ):
        self.script_path = script_path or DEFAULT_OCR_SCRIPT
        self.language = language
        self.timeout_s = timeout_s
        self._run = run

    def recognize(self, image_path: Path) -> OcrResult:
        if not self.script_path.exists():
            raise OcrError(f"OCR script not found: {self.script_path}")
        if not image_path.exists():
            raise OcrError(f"OCR image not found: {image_path}")

        command = self._base_command() + ["-Image", str(image_path)]
        if self.language:
            command += ["-Language", self.language]

        started_at = time.perf_counter()
        payload = self._run_json(command)
        elapsed_s = max(time.perf_counter() - started_at, 0.0)

        language = payload.get("language")
        lines = _payload_lines(payload, language=language)
        text = "\n".join(lines).strip()
        log(
            "ocr completed",
            backend=payload.get("backend"),
            language=language,
            lines=len(lines),
            chars=len(text),
            elapsed_s=round(elapsed_s, 3),
        )
        return OcrResult(
            text=text,
            lines=lines,
            language=language,
            backend=str(payload.get("backend") or "windows-media-ocr"),
            elapsed_s=elapsed_s,
            image_width=payload.get("imageWidth"),
            image_height=payload.get("imageHeight"),
        )

    def list_languages(self) -> list[dict[str, Any]]:
        if not self.script_path.exists():
            raise OcrError(f"OCR script not found: {self.script_path}")
        payload = self._run_json(self._base_command() + ["-ListLanguages"])
        languages = payload.get("availableLanguages")
        return list(languages) if isinstance(languages, list) else []

    def _base_command(self) -> list[str]:
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
        ]

    def _run_json(self, command: list[str]) -> dict[str, Any]:
        try:
            completed = self._run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                # PowerShell host errors emitted before the script switches the
                # console to UTF-8 arrive in the OEM code page (cp949 here);
                # never let a decode error escape the OcrError contract.
                errors="replace",
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError(f"Windows OCR timed out after {self.timeout_s}s.") from exc

        if completed.returncode != 0:
            error_text = (completed.stderr or completed.stdout or "").strip()
            error_text = error_text.removeprefix("[ERROR]").strip()
            raise OcrError(error_text or "Windows OCR failed.")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise OcrError(
                f"OCR script returned invalid JSON: {completed.stdout!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise OcrError(f"OCR script returned non-object JSON: {payload!r}")
        return payload


def ocr_metrics(result: OcrResult) -> dict[str, Any]:
    return {
        "backend": result.backend,
        "language": result.language,
        "elapsed_s": result.elapsed_s,
        "lineCount": len(result.lines),
        "charCount": len(result.text),
    }


def _payload_lines(payload: dict[str, Any], *, language: str | None) -> list[str]:
    raw_lines = payload.get("lines")
    lines: list[str] = []
    if isinstance(raw_lines, list):
        for item in raw_lines:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                lines.append(item["text"])
    if not lines and isinstance(payload.get("text"), str):
        lines = [payload["text"]]

    # Windows OCR inserts spaces between recognized word segments, which is
    # wrong for spaceless scripts (Japanese/Chinese).
    if language and language.lower().startswith(SPACELESS_LANGUAGE_PREFIXES):
        lines = [line.replace(" ", "") for line in lines]
    return [line.strip() for line in lines if line.strip()]
