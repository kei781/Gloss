from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from gloss.backend.probe import BackendStatus
from gloss.system import SystemSample


@dataclass(frozen=True)
class Stats:
    count: int
    avg: float | None
    p50: float | None
    p95: float | None


@dataclass
class GroupSummary:
    key: str
    requests: int = 0
    truncated: int = 0
    source_chars: int = 0
    translated_chars: int = 0
    completion_tokens: int = 0
    prompt_tokens: int = 0
    token_sources: dict[str, int] = field(default_factory=dict)
    models: set[str] = field(default_factory=set)
    last_recorded_at: str | None = None
    ttft_values: list[float] = field(default_factory=list)
    decode_tps_values: list[float] = field(default_factory=list)
    e2e_tps_values: list[float] = field(default_factory=list)
    elapsed_values: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardSummary:
    total_requests: int
    bad_lines: int
    groups: list[GroupSummary]


def load_metrics_rows(paths: list[Path]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad_lines = 0
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                bad_lines += 1
    return rows, bad_lines


def summarize(rows: list[dict[str, Any]], *, bad_lines: int = 0) -> DashboardSummary:
    groups: dict[str, GroupSummary] = {}
    for row in rows:
        engine = str(row.get("engine") or "unknown")
        phase = row.get("phase")
        key = f"{engine}/phase{phase}" if phase is not None else engine
        group = groups.setdefault(key, GroupSummary(key=key))
        group.requests += 1
        group.source_chars += _as_int(row.get("sourceChars"))
        group.translated_chars += _as_int(row.get("translatedChars"))
        if isinstance(row.get("model"), str):
            group.models.add(row["model"])
        recorded = row.get("recordedAt")
        if isinstance(recorded, str):
            if group.last_recorded_at is None or recorded > group.last_recorded_at:
                group.last_recorded_at = recorded

        generation = row.get("generation")
        if not isinstance(generation, dict):
            continue
        group.completion_tokens += _as_int(generation.get("completion_tokens"))
        group.prompt_tokens += _as_int(generation.get("prompt_tokens"))
        if generation.get("truncated"):
            group.truncated += 1
        source = generation.get("token_count_source")
        if isinstance(source, str):
            group.token_sources[source] = group.token_sources.get(source, 0) + 1
        _append_value(group.ttft_values, generation.get("ttft_s"))
        _append_value(group.decode_tps_values, generation.get("tokens_per_second"))
        _append_value(group.e2e_tps_values, generation.get("end_to_end_tokens_per_second"))
        _append_value(group.elapsed_values, generation.get("elapsed_s"))

    ordered = sorted(groups.values(), key=lambda group: group.key)
    return DashboardSummary(
        total_requests=sum(group.requests for group in ordered),
        bad_lines=bad_lines,
        groups=ordered,
    )


def stats_of(values: list[float]) -> Stats:
    if not values:
        return Stats(count=0, avg=None, p50=None, p95=None)
    return Stats(
        count=len(values),
        avg=sum(values) / len(values),
        p50=percentile(values, 0.50),
        p95=percentile(values, 0.95),
    )


def percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile; stable for the small samples we collect."""
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def format_summary(
    summary: DashboardSummary,
    *,
    system: SystemSample | None = None,
    backend: BackendStatus | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Gloss Dashboard (FR-D1~D3)")
    lines.append("")
    if backend is not None:
        if backend.up:
            models = ", ".join(backend.models) or "(none reported)"
            lines.append(f"backend: UP - models: {models}")
        else:
            lines.append(f"backend: DOWN - {backend.error}")
    if system is not None:
        cpu = "n/a" if system.cpu_percent is None else f"{system.cpu_percent:.0f}%"
        lines.append(
            f"system: CPU {cpu} | RAM {system.ram_used_mb:.0f}/"
            f"{system.ram_total_mb:.0f} MB ({system.ram_percent:.0f}%)"
        )
        lines.append("(CPU 유휴는 NPU 가동의 보조 증거 - 단독 판정 근거 아님, ADR-009)")
    lines.append(f"requests: {summary.total_requests}  bad_lines: {summary.bad_lines}")
    lines.append("")

    for group in summary.groups:
        ttft = stats_of(group.ttft_values)
        decode = stats_of(group.decode_tps_values)
        e2e = stats_of(group.e2e_tps_values)
        elapsed = stats_of(group.elapsed_values)
        lines.append(f"## {group.key}")
        lines.append(
            f"  requests={group.requests} truncated={group.truncated} "
            f"models={', '.join(sorted(group.models)) or '-'}"
        )
        lines.append(
            f"  tokens out={group.completion_tokens} in={group.prompt_tokens} "
            f"chars src={group.source_chars} out={group.translated_chars}"
        )
        lines.append(
            f"  ttft_s {_fmt(ttft)} | decode_tok/s {_fmt(decode)} | "
            f"e2e_tok/s {_fmt(e2e)} | elapsed_s {_fmt(elapsed)}"
        )
        sources = ", ".join(
            f"{name}={count}" for name, count in sorted(group.token_sources.items())
        )
        lines.append(f"  token_count_source: {sources or '-'}")
        if group.last_recorded_at:
            lines.append(f"  last: {group.last_recorded_at}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt(stats: Stats) -> str:
    if stats.count == 0:
        return "-"
    return (
        f"avg {stats.avg:.2f} p50 {stats.p50:.2f} p95 {stats.p95:.2f} (n={stats.count})"
    )


def _append_value(values: list[float], value: Any) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(float(value))


def _as_int(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0
