from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class BackendStatus:
    up: bool
    models: list[str]
    error: str | None


def probe_backend(
    base_url: str,
    *,
    api_key: str = "local",
    timeout_s: float = 3.0,
    opener: Callable[..., Any] = urlopen,
) -> BackendStatus:
    """GET <base_url>/models against the OpenAI-compatible backend (FR-D1)."""
    request = Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with opener(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    # urllib only wraps pre-response failures into URLError; getresponse()/
    # read() can raise raw HTTPException (BadStatusLine, IncompleteRead).
    # ValueError covers JSONDecodeError and UnicodeDecodeError.
    except (HTTPError, URLError, TimeoutError, OSError, HTTPException, ValueError) as exc:
        return BackendStatus(up=False, models=[], error=str(exc))

    models: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
    return BackendStatus(up=True, models=models, error=None)
