from __future__ import annotations

import json
import os
import sys
import time
from typing import TextIO


def log(message: str, level: str = "INFO", **fields: object) -> None:
    level_name = level.upper()
    stream: TextIO = sys.stderr
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "level": level_name,
        "message": str(message),
        **fields,
    }

    if os.environ.get("GLOSS_LOG_FORMAT", "plain").lower() == "json":
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    else:
        details = " ".join(
            f"{key}={value}" for key, value in fields.items() if value is not None
        )
        suffix = f" {details}" if details else ""
        stream.write(f"[{level_name}] {message}{suffix}\n")
    stream.flush()
