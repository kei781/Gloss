from __future__ import annotations

import base64
from dataclasses import asdict
from pathlib import Path

from gloss.backend.openai_client import GenerationResult, OpenAIChatClient
from gloss.config import RuntimeConfig
from gloss.log import log
from gloss.metrics import MetricsRecorder, new_request_id
from gloss.visual.models import CaptureResult, VisualTranslation


VISUAL_SYSTEM_PROMPT = (
    "You are Gloss, an on-device visual translation engine. The OCR helper has "
    "provided text visible in a captured screen region. Translate it into natural "
    "Korean. Output only the translated Korean text. Do not add explanations, "
    "quotes, labels, source text, romanization, apologies, or notes."
)
VLM_SYSTEM_PROMPT = (
    "You are Gloss, an on-device visual translation engine. Read all foreign "
    "text visible in the image and translate it into natural Korean. Output "
    "only the translated Korean text. Do not add explanations, quotes, labels, "
    "source text, romanization, apologies, or notes."
)


class VisualEngine:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        client: OpenAIChatClient,
        metrics: MetricsRecorder,
        dry_run: bool = False,
    ):
        self.config = config
        self.client = client
        self.metrics = metrics
        self.dry_run = dry_run

    def translate_ocr_text(
        self,
        source_text: str,
        *,
        capture: CaptureResult | None = None,
        stream: bool = True,
        input_mode: str = "ocr_text",
        phase: int = 2,
        metrics_extra: dict[str, object] | None = None,
    ) -> VisualTranslation:
        clean_source = source_text.strip()
        if not clean_source:
            raise VisualEngineError("No OCR text provided for visual translation.")

        messages = [
            {"role": "system", "content": VISUAL_SYSTEM_PROMPT},
            {"role": "user", "content": clean_source},
        ]
        return self._translate(
            messages=messages,
            source_text=clean_source,
            capture=capture,
            stream=stream,
            input_mode=input_mode,
            phase=phase,
            metrics_extra=metrics_extra,
        )

    def translate_image(
        self,
        image_path: Path,
        *,
        capture: CaptureResult | None = None,
        stream: bool = True,
        phase: int = 2,
        metrics_extra: dict[str, object] | None = None,
    ) -> VisualTranslation:
        suffix = image_path.suffix.lower()
        media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix)
        if media_type is None:
            raise VisualEngineError("VLM image must be PNG or JPEG.")
        try:
            image_bytes = image_path.read_bytes()
        except OSError as exc:
            raise VisualEngineError(f"Cannot read VLM image: {image_path}: {exc}") from exc
        if not image_bytes:
            raise VisualEngineError(f"VLM image is empty: {image_path}")
        if len(image_bytes) > 20 * 1024 * 1024:
            raise VisualEngineError("VLM image exceeds 20 MB; crop the capture region.")

        encoded = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {"role": "system", "content": VLM_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Translate the visible text into Korean."},
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
            ]},
        ]
        return self._translate(
            messages=messages,
            source_text="",
            capture=capture,
            stream=stream,
            input_mode="vlm_image",
            phase=phase,
            metrics_extra={"image": {"path": str(image_path), "bytes": len(image_bytes)}, **(metrics_extra or {})},
        )

    def _translate(
        self,
        *,
        messages: list[dict[str, object]],
        source_text: str,
        capture: CaptureResult | None,
        stream: bool,
        input_mode: str,
        phase: int,
        metrics_extra: dict[str, object] | None,
    ) -> VisualTranslation:
        request_id = new_request_id()
        log(
            "visual translation started",
            request_id=request_id,
            model=self.config.model,
            source_chars=len(source_text),
            input_mode=input_mode,
        )
        result = self._complete(messages, source_text=source_text, stream=stream)
        if result.truncated:
            log(
                "visual translation truncated",
                level="WARN",
                request_id=request_id,
                finish_reason=result.finish_reason,
                max_tokens=self.config.max_tokens,
            )

        row: dict[str, object] = (
            {
                "requestId": request_id,
                "phase": phase,
                "engine": "visual",
                "inputMode": input_mode,
                "modelProfile": self.config.profile,
                "model": self.config.model,
                "backendBaseUrl": self.config.base_url,
                "maxTokens": self.config.max_tokens,
                "sourceChars": len(source_text),
                "translatedChars": len(result.text.strip()),
                "capture": _capture_to_dict(capture),
                "generation": asdict(result),
            }
        )
        if metrics_extra:
            row.update(metrics_extra)
        self.metrics.write(row)
        log("visual translation completed", request_id=request_id)
        return VisualTranslation(
            translated_text=result.text.strip(),
            source_text=source_text,
            capture=capture,
        )

    def _complete(
        self, messages: list[dict[str, object]], *, source_text: str, stream: bool
    ) -> GenerationResult:
        if self.dry_run:
            return GenerationResult(
                text=f"[DRY RUN]\n{source_text or '[image input]'}",
                elapsed_s=0.0,
                ttft_s=None,
                decode_window_s=None,
                completion_tokens=0,
                prompt_tokens=None,
                token_count_source="dry_run",
                tokens_per_second=None,
                end_to_end_tokens_per_second=None,
                chunks=0,
                usage=None,
                finish_reason=None,
                truncated=False,
            )

        return self.client.complete(
            messages=messages,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            stream=stream,
        )


class VisualEngineError(RuntimeError):
    pass


def _capture_to_dict(capture: CaptureResult | None) -> dict[str, object] | None:
    if capture is None:
        return None
    return {
        "backend": capture.backend,
        "imagePath": str(capture.image_path),
        "elapsed_s": capture.elapsed_s,
        "rect": asdict(capture.rect),
    }
