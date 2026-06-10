import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gloss.backend.probe import BackendStatus
from gloss.dashboard import cli as dashboard_cli
from gloss.system import SystemMetricsError, SystemSample


def _metrics_row():
    return {
        "engine": "visual",
        "phase": 2,
        "model": "phi-3.5-mini",
        "sourceChars": 10,
        "translatedChars": 12,
        "generation": {
            "ttft_s": 0.1,
            "tokens_per_second": 10.0,
            "end_to_end_tokens_per_second": 8.0,
            "elapsed_s": 1.0,
            "completion_tokens": 5,
            "prompt_tokens": 20,
            "token_count_source": "usage",
            "truncated": False,
        },
    }


class FakeSampler:
    def __init__(self):
        self.calls = 0

    def sample(self) -> SystemSample:
        self.calls += 1
        return SystemSample(
            cpu_percent=None if self.calls == 1 else 12.0,
            ram_used_mb=8000.0,
            ram_total_mb=32000.0,
            ram_percent=25.0,
        )


class DashboardCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.probe_urls: list[str] = []

        def fake_probe(base_url, **kwargs):
            self.probe_urls.append(base_url)
            return BackendStatus(up=True, models=["phi-3.5-mini"], error=None)

        for patch in (
            mock.patch.object(dashboard_cli, "WindowsSystemSampler", FakeSampler),
            mock.patch.object(dashboard_cli, "probe_backend", fake_probe),
            mock.patch.object(dashboard_cli.time, "sleep", lambda _s: None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _args(self, temp_dir: str, *extra: str) -> list[str]:
        metrics = Path(temp_dir) / "m.jsonl"
        metrics.write_text(json.dumps(_metrics_row()) + "\n", encoding="utf-8")
        return [
            "--metrics", str(metrics),
            "--env-file", str(Path(temp_dir) / "missing.env"),
            *extra,
        ]

    def test_once_survives_cp437_console(self) -> None:
        # Regression: cp949/cp437 consoles must not crash on summary glyphs.
        with tempfile.TemporaryDirectory() as temp_dir:
            buffer = io.BytesIO()
            stream = io.TextIOWrapper(buffer, encoding="cp437")
            with mock.patch.object(dashboard_cli.sys, "stdout", stream):
                with contextlib.redirect_stderr(io.StringIO()):
                    exit_code = dashboard_cli.main(self._args(temp_dir, "--once"))
            stream.flush()
            output = buffer.getvalue().decode("cp437", errors="replace")

        self.assertEqual(exit_code, 0)
        self.assertIn("# Gloss Dashboard", output)
        self.assertIn("visual/phase2", output)

    def test_once_stdout_carries_only_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                io.StringIO()
            ):
                exit_code = dashboard_cli.main(self._args(temp_dir, "--once"))
            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertTrue(output.startswith("# Gloss Dashboard"))
        self.assertNotIn("[INFO]", output)
        self.assertIn("backend: UP", output)

    def test_base_url_env_precedence(self) -> None:
        env = {
            "GLOSS_PHASE4_BASE_URL": "http://127.0.0.1:1/v1",
            "GLOSS_PHASE1_BASE_URL": "http://127.0.0.1:2/v1",
            "GLOSS_PHASE0_BASE_URL": "http://127.0.0.1:3/v1",
            "GLOSS_OPENAI_BASE_URL": "http://127.0.0.1:4/v1",
        }
        order = [
            ("GLOSS_PHASE4_BASE_URL", "http://127.0.0.1:1/v1"),
            ("GLOSS_PHASE1_BASE_URL", "http://127.0.0.1:2/v1"),
            ("GLOSS_PHASE0_BASE_URL", "http://127.0.0.1:3/v1"),
            ("GLOSS_OPENAI_BASE_URL", "http://127.0.0.1:4/v1"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, env, clear=False):
                for name, expected in order:
                    self.probe_urls.clear()
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                        io.StringIO()
                    ):
                        dashboard_cli.main(self._args(temp_dir, "--once"))
                    self.assertEqual(self.probe_urls[-1], expected)
                    os.environ.pop(name, None)

                self.probe_urls.clear()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    dashboard_cli.main(self._args(temp_dir, "--once"))
                self.assertEqual(self.probe_urls[-1], dashboard_cli.DEFAULT_BASE_URL)

    def test_sampler_failure_returns_error(self) -> None:
        def broken_sampler():
            raise SystemMetricsError("no win32")

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(dashboard_cli, "WindowsSystemSampler", broken_sampler):
                with contextlib.redirect_stderr(io.StringIO()):
                    exit_code = dashboard_cli.main(self._args(temp_dir, "--once"))

        self.assertEqual(exit_code, 1)

    def test_live_exit_codes(self) -> None:
        class _Boom:
            def __init__(self, **kwargs):
                self.error = None

            def run(self):
                raise _Boom.error

        with tempfile.TemporaryDirectory() as temp_dir:
            _Boom.error = KeyboardInterrupt()
            with mock.patch.object(dashboard_cli, "LiveDashboard", _Boom):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        dashboard_cli.main(self._args(temp_dir, "--max-ticks", "1")), 0
                    )

            _Boom.error = OSError("disk gone")
            with mock.patch.object(dashboard_cli, "LiveDashboard", _Boom):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        dashboard_cli.main(self._args(temp_dir, "--max-ticks", "1")), 1
                    )


if __name__ == "__main__":
    unittest.main()
