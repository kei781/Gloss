from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from gloss.env import env_value, load_env_file


DEFAULT_CONFIG_PATH = Path("phase0/config.example.json")
DEFAULT_ENV_PATH = Path("phase0/.env")
DEFAULT_PROFILES_PATH = Path("phase0/model-profiles.json")


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str | None
    model: str
    base_url: str
    api_key: str
    metrics_path: Path
    max_tokens: int
    temperature: float
    timeout_s: float
    config_path: Path | None
    env_file: Path | None


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def resolve_path(path_value: str | Path, base_dir: Path | None = None) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path

    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path

    if base_dir is not None:
        base_path = base_dir / path
        if base_path.exists():
            return base_path

    return cwd_path


def first_defined(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default


def _nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def load_runtime_config(
    *,
    config_path: Path | None = None,
    env_file: Path | None = None,
    profile: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    metrics_path: Path | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float | None = None,
) -> RuntimeConfig:
    if env_file is None:
        env_name = env_value("GLOSS_ENV_FILE", "GLOSS_PHASE1_ENV_FILE")
        env_file = resolve_path(env_name or DEFAULT_ENV_PATH)
    loaded_env = load_env_file(env_file)
    resolved_env = env_file if loaded_env else None

    if config_path is None:
        config_name = env_value("GLOSS_PHASE1_CONFIG", "GLOSS_PHASE0_CONFIG")
        config_path = resolve_path(config_name or DEFAULT_CONFIG_PATH)

    config: dict[str, Any] = {}
    config_dir = Path.cwd()
    resolved_config: Path | None = None
    if config_path.exists():
        resolved_config = config_path.resolve()
        config = load_json(resolved_config)
        config_dir = resolved_config.parent

    profiles_path_value = first_defined(
        env_value("GLOSS_PHASE1_MODEL_PROFILES_PATH", "GLOSS_PHASE0_MODEL_PROFILES_PATH"),
        config.get("model_profiles_path"),
        DEFAULT_PROFILES_PATH,
    )
    profiles_path = resolve_path(str(profiles_path_value), config_dir)
    profiles_doc = load_json(profiles_path) if profiles_path.exists() else {}

    selected_profile = first_defined(
        profile,
        env_value(
            "GLOSS_PHASE1_ACTIVE_MODEL_PROFILE",
            "GLOSS_PHASE0_ACTIVE_MODEL_PROFILE",
            "GLOSS_ACTIVE_MODEL_PROFILE",
        ),
        config.get("active_model_profile"),
        profiles_doc.get("default_profile"),
    )

    profile_doc: dict[str, Any] = {}
    profiles = profiles_doc.get("profiles")
    if selected_profile and isinstance(profiles, dict):
        candidate = profiles.get(str(selected_profile))
        if isinstance(candidate, dict):
            profile_doc = candidate

    selected_model = first_defined(
        model,
        env_value("GLOSS_PHASE1_MODEL", "GLOSS_PHASE0_MODEL", "GLOSS_MODEL"),
        _nested_get(profile_doc, "openai", "model"),
        profile_doc.get("runtime_model"),
    )
    if not selected_model:
        raise ValueError("No model configured. Provide --model or a model profile.")

    selected_base_url = first_defined(
        base_url,
        env_value("GLOSS_PHASE1_BASE_URL", "GLOSS_PHASE0_BASE_URL", "GLOSS_OPENAI_BASE_URL"),
        _nested_get(profile_doc, "serve", "base_url"),
        _nested_get(profile_doc, "openai", "base_url"),
        _nested_get(config, "backend", "base_url"),
        default="http://127.0.0.1:11435/v1",
    )

    selected_api_key = first_defined(
        api_key,
        env_value("GLOSS_PHASE1_API_KEY", "GLOSS_PHASE0_API_KEY", "OPENAI_API_KEY"),
        _nested_get(config, "backend", "api_key"),
        default="local",
    )

    selected_metrics_path = Path(
        first_defined(
            metrics_path,
            env_value("GLOSS_PHASE1_METRICS_PATH"),
            default="runs/phase1/text-metrics.jsonl",
        )
    )

    selected_max_tokens = int(
        first_defined(
            max_tokens,
            env_value("GLOSS_PHASE1_MAX_TOKENS"),
            _nested_get(profile_doc, "measurement", "max_tokens"),
            _nested_get(config, "benchmarks", "max_tokens"),
            default=512,
        )
    )

    selected_temperature = float(
        first_defined(
            temperature,
            env_value("GLOSS_PHASE1_TEMPERATURE"),
            default=0.0,
        )
    )

    selected_timeout = float(
        first_defined(
            timeout_s,
            env_value("GLOSS_PHASE1_TIMEOUT"),
            default=180.0,
        )
    )

    return RuntimeConfig(
        profile=str(selected_profile) if selected_profile else None,
        model=str(selected_model),
        base_url=str(selected_base_url),
        api_key=str(selected_api_key),
        metrics_path=selected_metrics_path,
        max_tokens=selected_max_tokens,
        temperature=selected_temperature,
        timeout_s=selected_timeout,
        config_path=resolved_config,
        env_file=resolved_env,
    )
