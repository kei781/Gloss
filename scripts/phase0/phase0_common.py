from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TextIO


def log(message: str, level: str = "INFO", *, error: bool = False) -> None:
    level_name = level.upper()
    stream: TextIO = sys.stderr if error or level_name in {"ERROR", "WARN"} else sys.stdout
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "level": level_name,
        "message": str(message),
    }

    if os.environ.get("GLOSS_LOG_FORMAT", "plain").lower() == "json":
        print(json.dumps(record, ensure_ascii=False), file=stream)
    else:
        print(f"[{record['level']}] {record['message']}", file=stream)


def env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return None


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            os.environ.setdefault(key, value)
            loaded[key] = value

    return loaded
