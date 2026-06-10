import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from gloss.backend.probe import BackendStatus
from gloss.dashboard.live import (
    CpuWindow,
    JsonlTail,
    LiveConfig,
    LiveDashboard,
    assess_cpu_fallback,
)
from gloss.system import SystemSample


class JsonlTailTest(unittest.TestCase):
    def test_reads_only_complete_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)

            self.assertEqual(tail.read_new(), [])

            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"a": 1}\n{"b": ')
            rows = tail.read_new()
            self.assertEqual(rows, [{"a": 1}])

            with path.open("a", encoding="utf-8") as handle:
                handle.write('2}\n')
            rows = tail.read_new()
            self.assertEqual(rows, [{"b": 2}])

    def test_truncation_resets_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)
            path.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
            self.assertEqual(len(tail.read_new()), 2)

            path.write_text('{"a": 3}\n', encoding="utf-8")
            rows = tail.read_new()

        self.assertEqual(rows, [{"a": 3}])

    def test_preexisting_rows_skipped(self) -> None:
        # tail -f semantics: rows present at construction belong to --once.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            path.write_text('{"old": 1}\n', encoding="utf-8")
            tail = JsonlTail(path)

            self.assertEqual(tail.read_new(), [])

            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"new": 2}\n')
            rows = tail.read_new()

        self.assertEqual(rows, [{"new": 2}])

    def test_delete_then_recreate_resets_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)
            path.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
            self.assertEqual(len(tail.read_new()), 2)

            path.unlink()
            self.assertEqual(tail.read_new(), [])

            path.write_text('{"b": 1}\n', encoding="utf-8")
            rows = tail.read_new()

        self.assertEqual(rows, [{"b": 1}])

    def test_recreate_larger_than_old_offset_not_skipped(self) -> None:
        # New file identity must reset the offset even when the replacement
        # grows past the stale offset within one tick.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)
            path.write_text('{"a": 1}\n', encoding="utf-8")
            self.assertEqual(len(tail.read_new()), 1)

            path.unlink()
            replacement = '{"b": 1}\n{"b": 2}\n{"b": 3}\n'
            path.write_text(replacement, encoding="utf-8")
            rows = tail.read_new()

        self.assertEqual(rows, [{"b": 1}, {"b": 2}, {"b": 3}])


class CpuWindowTest(unittest.TestCase):
    def test_average_over_window(self) -> None:
        window = CpuWindow()
        window.add(1.0, 10.0)
        window.add(2.0, 20.0)
        window.add(3.0, 90.0)

        self.assertEqual(window.average_over(0.5, 2.5), 15.0)
        self.assertIsNone(window.average_over(10.0, 20.0))

    def test_none_samples_ignored(self) -> None:
        window = CpuWindow()
        window.add(1.0, None)

        self.assertIsNone(window.average_over(0.0, 2.0))


class AssessCpuFallbackTest(unittest.TestCase):
    def test_high_cpu_flags(self) -> None:
        row = {"generation": {"token_count_source": "usage"}}

        self.assertTrue(assess_cpu_fallback(row, avg_cpu=80.0, threshold=65.0))
        self.assertFalse(assess_cpu_fallback(row, avg_cpu=30.0, threshold=65.0))

    def test_dry_run_and_missing_data_ignored(self) -> None:
        dry = {"generation": {"token_count_source": "dry_run"}}

        self.assertFalse(assess_cpu_fallback(dry, avg_cpu=99.0, threshold=65.0))
        self.assertFalse(assess_cpu_fallback({}, avg_cpu=99.0, threshold=65.0))
        self.assertFalse(
            assess_cpu_fallback(
                {"generation": {}}, avg_cpu=None, threshold=65.0
            )
        )


class FakeSampler:
    def __init__(self, cpu_values):
        self._cpu_values = list(cpu_values)
        self.calls = 0

    def sample(self) -> SystemSample:
        index = min(self.calls, len(self._cpu_values) - 1)
        self.calls += 1
        return SystemSample(
            cpu_percent=self._cpu_values[index],
            ram_used_mb=8000.0,
            ram_total_mb=32000.0,
            ram_percent=25.0,
        )


class LiveDashboardTest(unittest.TestCase):
    def test_warns_on_high_cpu_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)
            row = {
                "requestId": "req-1",
                "engine": "text",
                "phase": 1,
                "model": "phi-3.5-mini",
                "generation": {
                    "elapsed_s": 2.0,
                    "tokens_per_second": 12.0,
                    "token_count_source": "usage",
                },
            }

            ticks = {"now": 0.0}

            def clock() -> float:
                return ticks["now"]

            def sleep(_seconds: float) -> None:
                ticks["now"] += 1.0

            probe_calls = []

            def probe(base_url, **kwargs):
                probe_calls.append(base_url)
                return BackendStatus(up=True, models=["phi-3.5-mini"], error=None)

            dashboard = LiveDashboard(
                tails=[tail],
                sampler=FakeSampler([90.0, 90.0, 90.0, 90.0]),
                base_url="http://127.0.0.1:11435/v1",
                config=LiveConfig(interval_s=1.0, cpu_threshold=65.0, max_ticks=3),
                probe=probe,
                sleep=sleep,
                clock=clock,
            )

            # Row appears before the second tick.
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                dashboard.run()

            output = stderr.getvalue()

        self.assertIn("request completed", output)
        self.assertIn("possible silent CPU fallback", output)
        self.assertIn("req-1", output)
        self.assertGreaterEqual(len(probe_calls), 1)

    def test_no_warning_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            tail = JsonlTail(path)
            row = {
                "engine": "text",
                "phase": 1,
                "generation": {
                    "elapsed_s": 1.0,
                    "token_count_source": "usage",
                },
            }
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            dashboard = LiveDashboard(
                tails=[tail],
                sampler=FakeSampler([10.0, 10.0, 10.0]),
                base_url="http://127.0.0.1:11435/v1",
                config=LiveConfig(max_ticks=2),
                probe=lambda base_url, **kwargs: BackendStatus(
                    up=False, models=[], error="down"
                ),
                sleep=lambda _seconds: None,
                clock=lambda: 100.0,
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                dashboard.run()

            output = stderr.getvalue()

        self.assertIn("request completed", output)
        # cpu_during must be computed (not None) — pins the window arithmetic
        self.assertIn("cpu_during=10.0", output)
        self.assertNotIn("possible silent CPU fallback", output)

    def test_sampler_failure_propagates(self) -> None:
        from gloss.system import SystemMetricsError

        class FailingSampler:
            def __init__(self):
                self.calls = 0

            def sample(self):
                self.calls += 1
                if self.calls >= 2:
                    raise SystemMetricsError("GetSystemTimes failed.")
                return SystemSample(
                    cpu_percent=None,
                    ram_used_mb=1.0,
                    ram_total_mb=2.0,
                    ram_percent=50.0,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            dashboard = LiveDashboard(
                tails=[JsonlTail(Path(temp_dir) / "missing.jsonl")],
                sampler=FailingSampler(),
                base_url="http://127.0.0.1:11435/v1",
                config=LiveConfig(max_ticks=3),
                probe=lambda base_url, **kwargs: BackendStatus(
                    up=False, models=[], error="down"
                ),
                sleep=lambda _seconds: None,
                clock=lambda: 1.0,
            )

            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemMetricsError):
                    dashboard.run()


if __name__ == "__main__":
    unittest.main()
