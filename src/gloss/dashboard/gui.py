"""Live desktop dashboard for Gloss metrics and Intel NPU activity."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sys
import time

from gloss.backend.probe import BackendStatus, probe_backend
from gloss.dashboard.aggregate import format_summary, load_metrics_rows, summarize
from gloss.dashboard.cli import DEFAULT_BASE_URL, DEFAULT_METRICS
from gloss.env import env_value, load_env_file
from gloss.system import SystemSample, WindowsSystemSampler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open the Gloss desktop dashboard.")
    parser.add_argument("--metrics", type=Path, action="append")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--npu-luid", help="Intel AI Boost LUID, e.g. 0x11b60")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds.")
    return parser


def decode_sparkline(rows: list[dict], limit: int = 40) -> str:
    values: list[float] = []
    recent = sorted(rows, key=lambda row: str(row.get("recordedAt") or ""))[-limit * 3:]
    for row in recent:
        generation = row.get("generation")
        if not isinstance(generation, dict):
            continue
        rate = generation.get("tokens_per_second")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0:
            values.append(float(rate))
    values = values[-limit:]
    if not values:
        return "decode tok/s: no samples"
    peak = max(values) or 1.0
    blocks = "▁▂▃▄▅▆▇█"
    chart = "".join(blocks[min(7, int(rate / peak * 7))] for rate in values)
    return f"decode tok/s: {chart}  latest {values[-1]:.1f}  peak {peak:.1f}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 0.5:
        raise SystemExit("--interval must be at least 0.5 seconds")
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import (
            QApplication, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
            QPushButton, QVBoxLayout, QWidget,
        )
    except ImportError:
        print("Desktop dashboard requires: pip install -e '.[gui]'", file=sys.stderr)
        return 1

    load_env_file(args.env_file or Path("phase0/.env"))
    base_url = args.base_url or env_value(
        "GLOSS_PHASE4_BASE_URL", "GLOSS_PHASE1_BASE_URL", "GLOSS_PHASE0_BASE_URL",
        "GLOSS_OPENAI_BASE_URL",
    ) or DEFAULT_BASE_URL
    api_key = args.api_key or env_value(
        "GLOSS_PHASE4_API_KEY", "GLOSS_PHASE1_API_KEY", "GLOSS_PHASE0_API_KEY",
        "OPENAI_API_KEY",
    ) or "local"
    luid_text = args.npu_luid or env_value("GLOSS_NPU_LUID")
    try:
        luid = int(luid_text, 0) if luid_text else None
    except ValueError:
        raise SystemExit("--npu-luid must be an integer such as 0x11b60")
    sampler = WindowsSystemSampler(npu_luid=luid)
    paths = args.metrics or DEFAULT_METRICS

    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle("Gloss · Dashboard")
    window.resize(960, 720)
    root = QWidget()
    layout = QVBoxLayout(root)
    header = QHBoxLayout()
    title = QLabel("Gloss · Intel NPU")
    title.setStyleSheet("font-size: 20px; font-weight: 600")
    refresh = QPushButton("Refresh")
    header.addWidget(title)
    header.addStretch(1)
    header.addWidget(refresh)
    layout.addLayout(header)
    status = QLabel("Collecting…")
    layout.addWidget(status)
    chart = QLabel("decode tok/s: no samples")
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    chart.setFont(font)
    layout.addWidget(chart)
    detail = QPlainTextEdit()
    detail.setReadOnly(True)
    detail.setFont(font)
    layout.addWidget(detail, 1)
    window.setCentralWidget(root)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gloss-dashboard")
    pending: Future | None = None
    last_probe: BackendStatus | None = None
    last_probe_at = 0.0

    def collect():
        nonlocal last_probe, last_probe_at
        system: SystemSample = sampler.sample()
        rows, bad_lines = load_metrics_rows(paths)
        now = time.monotonic()
        if last_probe is None or now - last_probe_at >= 30:
            last_probe = probe_backend(base_url, api_key=api_key)
            last_probe_at = now
        return format_summary(summarize(rows, bad_lines=bad_lines), system=system, backend=last_probe), decode_sparkline(rows)

    def request_refresh() -> None:
        nonlocal pending
        if pending is None:
            pending = executor.submit(collect)

    def poll() -> None:
        nonlocal pending
        if pending is None or not pending.done():
            return
        finished = pending
        pending = None
        try:
            summary_text, chart_text = finished.result()
        except Exception as exc:
            status.setText(f"Refresh failed: {exc}")
            return
        detail.setPlainText(summary_text)
        chart.setText(chart_text)
        status.setText(f"Updated {time.strftime('%H:%M:%S')} · {base_url}")

    refresh.clicked.connect(request_refresh)
    timer = QTimer(window)
    timer.timeout.connect(request_refresh)
    timer.start(int(args.interval * 1000))
    result_timer = QTimer(window)
    result_timer.timeout.connect(poll)
    result_timer.start(100)
    request_refresh()
    window.show()
    try:
        return app.exec()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())
