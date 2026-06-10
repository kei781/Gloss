from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from gloss.backend.probe import probe_backend
from gloss.dashboard.aggregate import format_summary, load_metrics_rows, summarize
from gloss.dashboard.live import JsonlTail, LiveConfig, LiveDashboard
from gloss.env import env_value, load_env_file
from gloss.log import log
from gloss.system import SystemMetricsError, WindowsSystemSampler


DEFAULT_METRICS = [
    Path("runs/phase1/text-metrics.jsonl"),
    Path("runs/phase2/visual-metrics.jsonl"),
    Path("runs/phase3/watch-metrics.jsonl"),
]
DEFAULT_BASE_URL = "http://127.0.0.1:11435/v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gloss Phase 4 dashboard: aggregate call-path metrics (FR-D1/D2), "
            "sample CPU/RAM (FR-D3), and watch for silent CPU fallback (FR-D6)."
        )
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        action="append",
        help="Metrics JSONL path (repeatable). Defaults to the phase 1-3 files.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print an aggregate summary to stdout and exit.",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Live tick seconds.")
    parser.add_argument(
        "--cpu-threshold",
        type=float,
        default=65.0,
        help="Avg CPU%% during generation that triggers the fallback warning.",
    )
    parser.add_argument(
        "--probe-every",
        type=float,
        default=30.0,
        help="Seconds between backend /models probes in live mode.",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        help="Stop live mode after N ticks (default: run until Ctrl+C).",
    )
    parser.add_argument("--base-url", help="OpenAI-compatible base URL override.")
    parser.add_argument("--api-key", help="API key override.")
    parser.add_argument("--env-file", type=Path, help="Env file path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Windows consoles often run cp949/cp437; never crash on summary glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    env_file = args.env_file or Path("phase0/.env")
    if load_env_file(env_file):
        log("loaded env file", path=str(env_file))

    base_url = (
        args.base_url
        or env_value(
            "GLOSS_PHASE4_BASE_URL",
            "GLOSS_PHASE1_BASE_URL",
            "GLOSS_PHASE0_BASE_URL",
            "GLOSS_OPENAI_BASE_URL",
        )
        or DEFAULT_BASE_URL
    )
    api_key = args.api_key or env_value("GLOSS_PHASE0_API_KEY", "OPENAI_API_KEY") or "local"
    metrics_paths = args.metrics or DEFAULT_METRICS

    try:
        sampler = WindowsSystemSampler()
    except SystemMetricsError as exc:
        log(str(exc), level="ERROR")
        return 1

    if args.once:
        rows, bad_lines = load_metrics_rows(metrics_paths)
        summary = summarize(rows, bad_lines=bad_lines)
        sampler.sample()
        time.sleep(0.3)  # CPU% needs two samples
        system = sampler.sample()
        backend = probe_backend(base_url, api_key=api_key)
        sys.stdout.write(format_summary(summary, system=system, backend=backend))
        return 0

    dashboard = LiveDashboard(
        tails=[JsonlTail(path) for path in metrics_paths],
        sampler=sampler,
        base_url=base_url,
        api_key=api_key,
        config=LiveConfig(
            interval_s=args.interval,
            cpu_threshold=args.cpu_threshold,
            probe_every_s=args.probe_every,
            max_ticks=args.max_ticks,
        ),
    )
    try:
        dashboard.run()
    except KeyboardInterrupt:
        log("dashboard stopped by user")
    except (SystemMetricsError, OSError) as exc:
        log(str(exc), level="ERROR")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
