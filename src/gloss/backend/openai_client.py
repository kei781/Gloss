from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class GenerationResult:
    text: str
    elapsed_s: float
    ttft_s: float | None
    decode_window_s: float | None
    completion_tokens: int
    prompt_tokens: int | None
    token_count_source: str
    tokens_per_second: float | None
    end_to_end_tokens_per_second: float | None
    chunks: int
    usage: dict[str, Any] | None
    finish_reason: str | None
    truncated: bool


class OpenAIChatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float,
        chars_per_token: float = 3.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.chars_per_token = chars_per_token

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        stream: bool = True,
    ) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}

        if stream:
            return self._complete_stream(payload)
        return self._complete_non_stream(payload)

    def _post_json(self, payload: dict[str, Any]):
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        return urlopen(request, timeout=self.timeout_s)

    def _complete_stream(self, payload: dict[str, Any]) -> GenerationResult:
        started_at = time.perf_counter()
        first_token_at: float | None = None
        output_parts: list[str] = []
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None
        chunks = 0

        try:
            response = self._post_json(payload)
            with response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if event.get("usage"):
                        usage = event["usage"]

                    event_finish_reason = _extract_finish_reason(event)
                    if event_finish_reason:
                        finish_reason = event_finish_reason

                    text = _extract_delta_text(event)
                    if not text:
                        continue
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    chunks += 1
                    output_parts.append(text)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise BackendError(str(exc)) from exc

        ended_at = time.perf_counter()
        return _summarize_generation(
            output="".join(output_parts),
            usage=usage,
            started_at=started_at,
            first_token_at=first_token_at,
            ended_at=ended_at,
            chunks=chunks,
            chars_per_token=self.chars_per_token,
            stream=True,
            finish_reason=finish_reason,
        )

    def _complete_non_stream(self, payload: dict[str, Any]) -> GenerationResult:
        started_at = time.perf_counter()
        try:
            response = self._post_json(payload)
            with response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise BackendError(str(exc)) from exc

        ended_at = time.perf_counter()
        choices = data.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        finish_reason = _coerce_optional_str(choice.get("finish_reason"))
        content = message.get("content", "")
        if isinstance(content, list):
            output = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        else:
            output = str(content)

        return _summarize_generation(
            output=output,
            usage=data.get("usage"),
            started_at=started_at,
            first_token_at=None,
            ended_at=ended_at,
            chunks=0,
            chars_per_token=self.chars_per_token,
            stream=False,
            finish_reason=finish_reason,
        )


class BackendError(RuntimeError):
    pass


def _extract_delta_text(event: dict[str, Any]) -> str:
    choices = event.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return ""


def _extract_finish_reason(event: dict[str, Any]) -> str | None:
    choices = event.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    return _coerce_optional_str(choices[0].get("finish_reason"))


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _summarize_generation(
    *,
    output: str,
    usage: dict[str, Any] | None,
    started_at: float,
    first_token_at: float | None,
    ended_at: float,
    chunks: int,
    chars_per_token: float,
    stream: bool,
    finish_reason: str | None,
) -> GenerationResult:
    elapsed_s = max(ended_at - started_at, 0.0)
    ttft_s = None if first_token_at is None else max(first_token_at - started_at, 0.0)
    decode_window_s = (
        max(ended_at - first_token_at, 0.0)
        if first_token_at is not None
        else None
    )

    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    token_count_source = "missing"
    if usage:
        completion_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        if completion_tokens is not None:
            token_count_source = "usage"

    if completion_tokens is None:
        completion_tokens = max(1, math.ceil(len(output) / chars_per_token))
        token_count_source = "estimated_chars_per_token"

    tokens_per_second = (
        completion_tokens / decode_window_s
        if stream and decode_window_s and decode_window_s > 0
        else None
    )
    end_to_end_tokens_per_second = (
        completion_tokens / elapsed_s if elapsed_s > 0 else None
    )
    truncated = finish_reason == "length"

    return GenerationResult(
        text=output,
        elapsed_s=elapsed_s,
        ttft_s=ttft_s,
        decode_window_s=decode_window_s,
        completion_tokens=completion_tokens,
        prompt_tokens=prompt_tokens,
        token_count_source=token_count_source,
        tokens_per_second=tokens_per_second,
        end_to_end_tokens_per_second=end_to_end_tokens_per_second,
        chunks=chunks,
        usage=usage,
        finish_reason=finish_reason,
        truncated=truncated,
    )
