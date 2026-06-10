from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Callable, Protocol

from gloss.backend.probe import BackendStatus, probe_backend
from gloss.log import log
from gloss.system import SystemSample


class Sampler(Protocol):
    def sample(self) -> SystemSample: ...


class JsonlTail:
    """Incremental reader for an append-only JSONL file (tail -f semantics).

    Rows that already exist when the tail is constructed are skipped; use the
    --once summary for historical data. Only complete lines (ending in a
    newline) are consumed, so a row that is mid-write is picked up on the
    next tick instead of being dropped. Transient filesystem errors skip the
    tick instead of killing a long-running dashboard.
    """

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._file_id: tuple[int, int] | None = None
        try:
            stat = path.stat()
            self._offset = path.read_bytes().rfind(b"\n") + 1
            self._file_id = (stat.st_dev, stat.st_ino)
        except OSError:
            pass

    def read_new(self) -> list[dict[str, Any]]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._offset = 0
            self._file_id = None
            return []
        except OSError:
            return []

        file_id = (stat.st_dev, stat.st_ino)
        if self._file_id is not None and file_id != self._file_id:
            self._offset = 0  # delete-and-recreate rotation: new file identity
        self._file_id = file_id
        if stat.st_size < self._offset:  # truncated in place
            self._offset = 0
        if stat.st_size == self._offset:
            return []

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except FileNotFoundError:
            self._offset = 0
            self._file_id = None
            return []
        except OSError:
            return []  # transient lock; retry next tick at the same offset
        last_newline = data.rfind(b"\n")
        if last_newline == -1:
            return []
        complete = data[: last_newline + 1]
        self._offset += last_newline + 1

        rows: list[dict[str, Any]] = []
        for raw_line in complete.splitlines():
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


class CpuWindow:
    """Ring buffer of (monotonic_ts, cpu_percent) samples."""

    def __init__(self, max_samples: int = 600):
        self._samples: deque[tuple[float, float]] = deque(maxlen=max_samples)

    def add(self, timestamp: float, cpu_percent: float | None) -> None:
        if cpu_percent is not None:
            self._samples.append((timestamp, cpu_percent))

    def average_over(self, start_ts: float, end_ts: float) -> float | None:
        values = [cpu for ts, cpu in self._samples if start_ts <= ts <= end_ts]
        if not values:
            return None
        return sum(values) / len(values)


def assess_cpu_fallback(
    row: dict[str, Any], avg_cpu: float | None, threshold: float
) -> bool:
    """FR-D6 heuristic: high CPU during generation suggests a CPU fallback.

    Supporting evidence only (ADR-009) — direct NPU evidence stays the
    authoritative signal; the NPU counter path is FR-D4 (Phase 5).
    """
    generation = row.get("generation")
    if not isinstance(generation, dict) or avg_cpu is None:
        return False
    if generation.get("token_count_source") == "dry_run":
        return False
    return avg_cpu >= threshold


@dataclass(frozen=True)
class LiveConfig:
    interval_s: float = 1.0
    cpu_threshold: float = 65.0
    probe_every_s: float = 30.0
    max_ticks: int | None = None


class LiveDashboard:
    def __init__(
        self,
        *,
        tails: list[JsonlTail],
        sampler: Sampler,
        base_url: str,
        api_key: str = "local",
        config: LiveConfig | None = None,
        probe: Callable[..., BackendStatus] = probe_backend,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.tails = tails
        self.sampler = sampler
        self.base_url = base_url
        self.api_key = api_key
        self.config = config or LiveConfig()
        self.probe = probe
        self.sleep = sleep
        self.clock = clock
        self.window = CpuWindow()

    def run(self) -> None:
        log(
            "dashboard live started",
            base_url=self.base_url,
            files=len(self.tails),
            cpu_threshold=self.config.cpu_threshold,
        )
        ticks = 0
        last_probe_at: float | None = None
        last_sample: SystemSample | None = None
        while self.config.max_ticks is None or ticks < self.config.max_ticks:
            ticks += 1
            now = self.clock()
            sample = self.sampler.sample()
            last_sample = sample
            self.window.add(now, sample.cpu_percent)

            for tail in self.tails:
                for row in tail.read_new():
                    self._handle_row(row, now)

            if last_probe_at is None or now - last_probe_at >= self.config.probe_every_s:
                last_probe_at = now
                self._log_status(sample)

            if self.config.max_ticks is not None and ticks >= self.config.max_ticks:
                break
            self.sleep(self.config.interval_s)

        if last_sample is not None:
            self._log_status(last_sample)
        log("dashboard live stopped", ticks=ticks)

    def _handle_row(self, row: dict[str, Any], now: float) -> None:
        generation = row.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        elapsed = generation.get("elapsed_s")
        elapsed_s = float(elapsed) if isinstance(elapsed, (int, float)) else 0.0
        avg_cpu = self.window.average_over(now - elapsed_s - 1.0, now)

        log(
            "request completed",
            engine=row.get("engine"),
            phase=row.get("phase"),
            model=row.get("model"),
            decode_tok_s=_round(generation.get("tokens_per_second")),
            ttft_s=_round(generation.get("ttft_s")),
            elapsed_s=_round(generation.get("elapsed_s")),
            tokens_out=generation.get("completion_tokens"),
            token_source=generation.get("token_count_source"),
            truncated=generation.get("truncated"),
            cpu_during=_round(avg_cpu),
        )
        if assess_cpu_fallback(row, avg_cpu, self.config.cpu_threshold):
            log(
                "possible silent CPU fallback (보조 증거, ADR-009/FR-D6)",
                level="WARN",
                cpu_during=_round(avg_cpu),
                threshold=self.config.cpu_threshold,
                request_id=row.get("requestId"),
            )

    def _log_status(self, sample: SystemSample) -> None:
        status = self.probe(self.base_url, api_key=self.api_key)
        log(
            "status",
            backend="up" if status.up else "down",
            models=",".join(status.models) if status.models else None,
            backend_error=status.error,
            cpu=_round(sample.cpu_percent),
            ram_mb=_round(sample.ram_used_mb),
            ram_percent=_round(sample.ram_percent),
        )


def _round(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    return None
