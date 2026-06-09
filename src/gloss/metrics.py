from __future__ import annotations

import json
from pathlib import Path
import time
import uuid
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class MetricsRecorder:
    def __init__(self, path: Path):
        self.path = path

    def write(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"recordedAt": now_iso(), **row}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
