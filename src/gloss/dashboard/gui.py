"""Live desktop dashboard for Gloss metrics and Intel NPU activity."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sys
import time

from gloss.backend.probe import BackendStatus, probe_backend
from gloss.dashboard.aggregate import SummaryAccumulator, format_summary
from gloss.dashboard.cli import resolve_dashboard_settings
from gloss.dashboard.live import JsonlTail
from gloss.system import SystemMetricsError, SystemSample, WindowsSystemSampler


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
    valid: list[tuple[str, float]] = []
    for row in rows:
        generation = row.get("generation")
        if not isinstance(generation, dict):
            continue
        rate = generation.get("tokens_per_second")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0:
            valid.append((str(row.get("recordedAt") or ""), float(rate)))
    values = [rate for _timestamp, rate in sorted(valid)[-limit:]]
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

    try:
        settings = resolve_dashboard_settings(args)
        sampler = WindowsSystemSampler(npu_luid=settings.npu_luid)
    except (ValueError, SystemMetricsError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

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
    tails = [JsonlTail(path, include_existing=True) for path in settings.metrics_paths]
    accumulator = SummaryAccumulator()
    recent_rows: list[dict] = []
    force_probe_next = False

    def collect(force_probe: bool = False):
        nonlocal last_probe, last_probe_at
        system: SystemSample = sampler.sample()
        rows = [row for tail in tails for row in tail.read_new()]
        for row in rows:
            accumulator.add(row)
        recent_rows.extend(
            row for row in rows
            if isinstance(row.get("generation"), dict)
            and isinstance(row["generation"].get("tokens_per_second"), (int, float))
            and not isinstance(row["generation"].get("tokens_per_second"), bool)
        )
        recent_rows[:] = sorted(
            recent_rows, key=lambda row: str(row.get("recordedAt") or "")
        )[-40:]
        now = time.monotonic()
        if force_probe or last_probe is None or now - last_probe_at >= 30:
            last_probe = probe_backend(settings.base_url, api_key=settings.api_key)
            last_probe_at = now
        summary = accumulator.snapshot(bad_lines=sum(tail.bad_lines for tail in tails))
        return format_summary(summary, system=system, backend=last_probe), decode_sparkline(recent_rows)

    def request_refresh(force_probe: bool = False) -> None:
        nonlocal pending, force_probe_next
        if pending is None:
            pending = executor.submit(collect, force_probe or force_probe_next)
            force_probe_next = False
        elif force_probe:
            force_probe_next = True

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
        if detail.toPlainText() != summary_text:
            vertical = detail.verticalScrollBar()
            horizontal = detail.horizontalScrollBar()
            vertical_value, horizontal_value = vertical.value(), horizontal.value()
            cursor = detail.textCursor()
            anchor, position = cursor.anchor(), cursor.position()
            has_selection = cursor.hasSelection()
            detail.setPlainText(summary_text)
            if has_selection:
                from PyQt6.QtGui import QTextCursor
                restored = detail.textCursor()
                restored.setPosition(min(anchor, len(summary_text)))
                restored.setPosition(min(position, len(summary_text)), QTextCursor.MoveMode.KeepAnchor)
                detail.setTextCursor(restored)
            vertical.setValue(vertical_value)
            horizontal.setValue(horizontal_value)
        chart.setText(chart_text)
        status.setText(f"Updated {time.strftime('%H:%M:%S')} · {settings.base_url}")
        if force_probe_next:
            request_refresh(True)

    refresh.clicked.connect(lambda: request_refresh(True))
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
