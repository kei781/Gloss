#!/usr/bin/env python
"""Measure an OpenAI-compatible local backend for Phase 0 validation.

The script intentionally uses only the Python standard library so it can run on
a fresh Windows ARM64 machine without installing packages.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--prompt",
        default=(
            "Translate this sentence into natural Korean and output only the "
            "translation: The moonlight fell softly over the old town."
        ),
    )
    parser.add_argument("--image", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=3.0,
        help="Fallback token estimate when the backend does not return usage.",
    )
    return parser.parse_args()


def image_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_messages(prompt: str, image: Path | None) -> list[dict[str, Any]]:
    if image is None:
        return [{"role": "user", "content": prompt}]

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
            ],
        }
    ]


def request_payload(args: argparse.Namespace, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "messages": build_messages(args.prompt, args.image),
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float):
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    return urlopen(request, timeout=timeout)


def extract_delta_text(event: dict[str, Any]) -> str:
    choices = event.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def measure_stream(args: argparse.Namespace, url: str) -> dict[str, Any]:
    start = time.perf_counter()
    first_token_at: float | None = None
    chunks = 0
    output_parts: list[str] = []
    usage: dict[str, Any] | None = None

    response = post_json(
        url,
        request_payload(args, stream=True),
        args.api_key,
        args.timeout,
    )

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

            text = extract_delta_text(event)
            if not text:
                continue
            if first_token_at is None:
                first_token_at = time.perf_counter()
            chunks += 1
            output_parts.append(text)

    end = time.perf_counter()
    return summarize_result(
        args=args,
        started_at=start,
        first_token_at=first_token_at,
        ended_at=end,
        output="".join(output_parts),
        chunks=chunks,
        usage=usage,
        stream=True,
    )


def measure_non_stream(args: argparse.Namespace, url: str) -> dict[str, Any]:
    start = time.perf_counter()
    response = post_json(
        url,
        request_payload(args, stream=False),
        args.api_key,
        args.timeout,
    )
    with response:
        data = json.loads(response.read().decode("utf-8"))
    end = time.perf_counter()

    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content", "")
    if isinstance(content, list):
        output = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    else:
        output = str(content)

    return summarize_result(
        args=args,
        started_at=start,
        first_token_at=None,
        ended_at=end,
        output=output,
        chunks=0,
        usage=data.get("usage"),
        stream=False,
    )


def summarize_result(
    *,
    args: argparse.Namespace,
    started_at: float,
    first_token_at: float | None,
    ended_at: float,
    output: str,
    chunks: int,
    usage: dict[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    elapsed_s = max(ended_at - started_at, 0.0)
    ttft_s = None if first_token_at is None else max(first_token_at - started_at, 0.0)
    decode_window_s = max(ended_at - (first_token_at or started_at), 0.0)

    completion_tokens = None
    prompt_tokens = None
    token_count_source = "missing"
    if usage:
        completion_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        if completion_tokens is not None:
            token_count_source = "usage"

    if completion_tokens is None:
        completion_tokens = max(1, math.ceil(len(output) / args.chars_per_token))
        token_count_source = "estimated_chars_per_token"

    tokens_per_second = (
        completion_tokens / decode_window_s if decode_window_s > 0 else None
    )

    return {
        "model": args.model,
        "has_image": args.image is not None,
        "stream": stream,
        "elapsed_s": elapsed_s,
        "ttft_s": ttft_s,
        "decode_window_s": decode_window_s,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "token_count_source": token_count_source,
        "tokens_per_second": tokens_per_second,
        "chunks": chunks,
        "output_chars": len(output),
        "output_preview": output[:500],
        "usage": usage,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_summary(rows: list[dict[str, Any]]) -> None:
    successful = [row for row in rows if row.get("ok")]
    print(f"runs: {len(rows)}")
    print(f"successful: {len(successful)}")
    if not successful:
        return

    tok_values = [
        row["result"]["tokens_per_second"]
        for row in successful
        if row["result"].get("tokens_per_second") is not None
    ]
    ttft_values = [
        row["result"]["ttft_s"]
        for row in successful
        if row["result"].get("ttft_s") is not None
    ]
    if tok_values:
        avg_tok = sum(tok_values) / len(tok_values)
        print(f"avg tokens_per_second: {avg_tok:.2f}")
    if ttft_values:
        avg_ttft = sum(ttft_values) / len(ttft_values)
        print(f"avg ttft_s: {avg_ttft:.2f}")


def main() -> int:
    args = parse_args()
    url = args.base_url.rstrip("/") + "/chat/completions"
    rows: list[dict[str, Any]] = []

    for index in range(1, args.runs + 1):
        started_wall = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            result = (
                measure_non_stream(args, url)
                if args.no_stream
                else measure_stream(args, url)
            )
            rows.append(
                {
                    "ok": True,
                    "run": index,
                    "startedAt": started_wall,
                    "result": result,
                }
            )
            tps = result.get("tokens_per_second")
            tps_text = "n/a" if tps is None else f"{tps:.2f}"
            print(f"run {index}: ok, tok/s={tps_text}")
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            rows.append(
                {
                    "ok": False,
                    "run": index,
                    "startedAt": started_wall,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            print(f"run {index}: failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    write_jsonl(args.output, rows)
    print(f"wrote: {args.output}")
    print_summary(rows)

    return 0 if all(row.get("ok") for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
