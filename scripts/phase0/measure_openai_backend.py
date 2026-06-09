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
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from phase0_common import env_value, load_env_file, log


DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_API_KEY = "local"
DEFAULT_PROMPT = (
    "Translate this sentence into natural Korean and output only the "
    "translation: The moonlight fell softly over the old town."
)
DEFAULT_RUNS = 3
DEFAULT_MAX_TOKENS = 128
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT = 120.0
DEFAULT_CHARS_PER_TOKEN = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="Phase 0 config JSON. Values can be overridden by CLI flags.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Env file containing Phase 0 keys. Defaults to config env_file or phase0/.env.",
    )
    parser.add_argument(
        "--profile",
        help="Model profile name from phase0/model-profiles.json.",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--prompt")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=None,
        help="Fallback token estimate when the backend does not return usage.",
    )
    args = parser.parse_args()
    apply_config(args)
    validate_args(args)
    return args


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def nested_get(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def first_defined(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default


def resolve_existing_or_cwd(path_value: str, config_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path

    from_cwd = Path.cwd() / path
    if from_cwd.exists():
        return from_cwd

    from_config = config_dir / path
    if from_config.exists():
        return from_config

    return from_cwd


def resolve_output_path(path_value: str | Path, config_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return resolve_existing_or_cwd(str(path), config_dir)


def sanitize_filename(value: str) -> str:
    allowed = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            allowed.append(char)
        else:
            allowed.append("-")
    return "".join(allowed).strip("-") or "measurement"


def load_profile(
    args: argparse.Namespace,
    config: dict[str, Any],
    config_dir: Path,
) -> tuple[str | None, dict[str, Any]]:
    profiles_path_value = first_defined(
        env_value("GLOSS_PHASE0_MODEL_PROFILES_PATH"),
        config.get("model_profiles_path"),
        default="phase0/model-profiles.json",
    )

    profiles_path = resolve_existing_or_cwd(str(profiles_path_value), config_dir)
    if not profiles_path.exists():
        if args.profile:
            raise FileNotFoundError(f"Model profiles file not found: {profiles_path}")
        return None, {}

    profiles_doc = load_json(profiles_path)
    profile_name = first_defined(
        args.profile,
        env_value("GLOSS_PHASE0_ACTIVE_MODEL_PROFILE", "GLOSS_ACTIVE_MODEL_PROFILE"),
        config.get("active_model_profile"),
        profiles_doc.get("default_profile"),
    )
    profiles = profiles_doc.get("profiles")
    if not profile_name or not isinstance(profiles, dict):
        return None, {}

    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        available = ", ".join(sorted(profiles))
        raise KeyError(f"Unknown model profile '{profile_name}'. Available: {available}")

    return str(profile_name), profile


def apply_config(args: argparse.Namespace) -> None:
    config: dict[str, Any] = {}
    config_dir = Path.cwd()
    profile_name: str | None = None
    profile: dict[str, Any] = {}

    if args.config:
        config_path = args.config.resolve()
        config = load_json(config_path)
        config_dir = config_path.parent

    env_file_value = first_defined(
        args.env_file,
        env_value("GLOSS_PHASE0_ENV_FILE"),
        config.get("env_file"),
        default="phase0/.env",
    )
    env_file = resolve_existing_or_cwd(str(env_file_value), config_dir)
    loaded_env = load_env_file(env_file)

    if loaded_env:
        args.env_file_loaded = str(env_file)
    else:
        args.env_file_loaded = None

    if not args.config:
        env_config = env_value("GLOSS_PHASE0_CONFIG")
        if env_config:
            config_path = resolve_existing_or_cwd(env_config, config_dir).resolve()
            config = load_json(config_path)
            config_dir = config_path.parent

    if (
        config
        or args.profile
        or env_value("GLOSS_PHASE0_ACTIVE_MODEL_PROFILE", "GLOSS_ACTIVE_MODEL_PROFILE")
    ):
        profile_name, profile = load_profile(args, config, config_dir)

    benchmarks = config.get("benchmarks") if isinstance(config.get("benchmarks"), dict) else {}
    measurement = (
        profile.get("measurement") if isinstance(profile.get("measurement"), dict) else {}
    )

    image_value = first_defined(args.image, measurement.get("image"))
    if image_value is not None:
        args.image = resolve_existing_or_cwd(str(image_value), config_dir)

    legacy_models = config.get("models") if isinstance(config.get("models"), dict) else {}
    legacy_model = legacy_models.get("vision" if args.image else "text")
    profile_model = first_defined(
        nested_get(profile, "openai", "model"),
        profile.get("runtime_model"),
    )

    config_prompt = benchmarks.get("vision_prompt" if args.image else "text_prompt")
    output_dir = first_defined(
        env_value("GLOSS_PHASE0_OUTPUT_DIR"),
        measurement.get("output_dir"),
        benchmarks.get("output_dir"),
        f"phase0/runs/{env_value('GLOSS_PHASE0_RUN_ID') or config.get('run_id', 'manual')}",
    )
    output_file = first_defined(
        env_value("GLOSS_PHASE0_OUTPUT_FILE"),
        measurement.get("output_file"),
        f"{sanitize_filename(profile_name or profile_model or legacy_model or 'manual')}.jsonl",
    )

    args.model_profile = profile_name
    args.model_profile_status = profile.get("status")
    args.base_url = first_defined(
        args.base_url,
        env_value("GLOSS_PHASE0_BASE_URL", "GLOSS_OPENAI_BASE_URL"),
        nested_get(profile, "serve", "base_url"),
        nested_get(profile, "openai", "base_url"),
        nested_get(config, "backend", "base_url"),
        default=DEFAULT_BASE_URL,
    )
    args.api_key = first_defined(
        args.api_key,
        env_value("GLOSS_PHASE0_API_KEY", "OPENAI_API_KEY"),
        nested_get(config, "backend", "api_key"),
        default=DEFAULT_API_KEY,
    )
    args.model = first_defined(
        args.model,
        env_value("GLOSS_PHASE0_MODEL", "GLOSS_MODEL"),
        profile_model,
        legacy_model,
    )
    args.prompt = first_defined(
        args.prompt,
        env_value("GLOSS_PHASE0_PROMPT"),
        measurement.get("prompt"),
        config_prompt,
        default=DEFAULT_PROMPT,
    )
    args.runs = int(
        first_defined(
            args.runs,
            env_value("GLOSS_PHASE0_RUNS"),
            measurement.get("runs"),
            benchmarks.get("runs"),
            default=DEFAULT_RUNS,
        )
    )
    args.max_tokens = int(
        first_defined(
            args.max_tokens,
            env_value("GLOSS_PHASE0_MAX_TOKENS"),
            measurement.get("max_tokens"),
            benchmarks.get("max_tokens"),
            default=DEFAULT_MAX_TOKENS,
        )
    )
    args.temperature = float(
        first_defined(
            args.temperature,
            env_value("GLOSS_PHASE0_TEMPERATURE"),
            default=DEFAULT_TEMPERATURE,
        )
    )
    args.timeout = float(
        first_defined(
            args.timeout,
            env_value("GLOSS_PHASE0_TIMEOUT"),
            default=DEFAULT_TIMEOUT,
        )
    )
    args.chars_per_token = float(
        first_defined(
            args.chars_per_token,
            env_value("GLOSS_PHASE0_CHARS_PER_TOKEN"),
            default=DEFAULT_CHARS_PER_TOKEN,
        )
    )
    if args.output is None:
        args.output = resolve_output_path(Path(str(output_dir)) / str(output_file), config_dir)


def validate_args(args: argparse.Namespace) -> None:
    missing: list[str] = []
    if not args.model:
        missing.append("--model or a model profile with runtime_model")
    if not args.output:
        missing.append("--output or benchmarks.output_dir")
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required value: {joined}")


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
    decode_window_s = (
        max(ended_at - first_token_at, 0.0)
        if first_token_at is not None
        else None
    )

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

    end_to_end_tokens_per_second = (
        completion_tokens / elapsed_s if elapsed_s > 0 else None
    )
    tokens_per_second = (
        completion_tokens / decode_window_s
        if stream and decode_window_s and decode_window_s > 0
        else None
    )
    gate_metric_eligible = (
        stream
        and token_count_source == "usage"
        and tokens_per_second is not None
    )

    return {
        "model_profile": getattr(args, "model_profile", None),
        "model_profile_status": getattr(args, "model_profile_status", None),
        "model": args.model,
        "base_url": args.base_url,
        "has_image": args.image is not None,
        "stream": stream,
        "elapsed_s": elapsed_s,
        "ttft_s": ttft_s,
        "decode_window_s": decode_window_s,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "token_count_source": token_count_source,
        "token_count_is_estimated": token_count_source != "usage",
        "tokens_per_second": tokens_per_second,
        "tokens_per_second_kind": (
            "stream_decode" if stream else "unavailable_non_stream"
        ),
        "end_to_end_tokens_per_second": end_to_end_tokens_per_second,
        "gate_metric_eligible": gate_metric_eligible,
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
    log(f"runs: {len(rows)}")
    log(f"successful: {len(successful)}")
    if not successful:
        return

    gate_tok_values = [
        row["result"]["tokens_per_second"]
        for row in successful
        if row["result"].get("gate_metric_eligible")
        and row["result"].get("tokens_per_second") is not None
    ]
    supporting_tok_values = [
        row["result"]["tokens_per_second"]
        for row in successful
        if not row["result"].get("gate_metric_eligible")
        and row["result"].get("tokens_per_second") is not None
    ]
    end_to_end_values = [
        row["result"]["end_to_end_tokens_per_second"]
        for row in successful
        if row["result"].get("end_to_end_tokens_per_second") is not None
    ]
    ttft_values = [
        row["result"]["ttft_s"]
        for row in successful
        if row["result"].get("ttft_s") is not None
    ]
    estimated_rows = [
        row
        for row in successful
        if row["result"].get("token_count_source") != "usage"
    ]
    non_stream_rows = [
        row for row in successful if not row["result"].get("stream")
    ]

    if gate_tok_values:
        avg_tok = sum(gate_tok_values) / len(gate_tok_values)
        log(f"avg gate-eligible decode tokens_per_second: {avg_tok:.2f}")
    if supporting_tok_values:
        avg_supporting = sum(supporting_tok_values) / len(supporting_tok_values)
        log(
            "avg supporting decode tokens_per_second: "
            f"{avg_supporting:.2f} (not gate-eligible)",
            level="WARN",
        )
    if end_to_end_values:
        avg_e2e = sum(end_to_end_values) / len(end_to_end_values)
        log(f"avg end_to_end_tokens_per_second: {avg_e2e:.2f}")
    if ttft_values:
        avg_ttft = sum(ttft_values) / len(ttft_values)
        log(f"avg ttft_s: {avg_ttft:.2f}")
    if estimated_rows:
        log(
            "token_count_source != usage; token counts are estimated and "
            "must be treated as supporting evidence only.",
            level="WARN",
        )
    if non_stream_rows:
        log(
            "--no-stream measures end-to-end throughput; decode tok/s is "
            "unavailable for Phase 0 gate use.",
            level="WARN",
        )


def main() -> int:
    args = parse_args()
    url = args.base_url.rstrip("/") + "/chat/completions"
    rows: list[dict[str, Any]] = []

    if getattr(args, "env_file_loaded", None):
        log(f"loaded env file: {args.env_file_loaded}")
    log(f"profile: {getattr(args, 'model_profile', None) or 'manual'}")
    log(f"model: {args.model}")
    log(f"base_url: {args.base_url}")

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
            log(f"run {index}: ok, tok/s={tps_text}")
            if not result.get("gate_metric_eligible"):
                reason = (
                    "non-stream mode"
                    if not result.get("stream")
                    else f"token_count_source={result.get('token_count_source')}"
                )
                log(
                    f"run {index}: not gate-eligible ({reason}); "
                    "use as supporting evidence only.",
                    level="WARN",
                )
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
            log(f"run {index}: failed: {type(exc).__name__}: {exc}", level="ERROR", error=True)

    write_jsonl(args.output, rows)
    log(f"wrote: {args.output}")
    print_summary(rows)

    return 0 if all(row.get("ok") for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
