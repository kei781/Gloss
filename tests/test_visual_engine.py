from pathlib import Path
import contextlib
import io
import tempfile
import unittest

from gloss.backend.openai_client import OpenAIChatClient
from gloss.config import RuntimeConfig
from gloss.metrics import MetricsRecorder
from gloss.visual.engine import VisualEngine


class VisualEngineTest(unittest.TestCase):
    def test_dry_run_writes_phase2_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metrics_path = root / "visual-metrics.jsonl"
            config = RuntimeConfig(
                profile="test",
                model="test-model",
                base_url="http://127.0.0.1:11435/v1",
                api_key="local",
                metrics_path=metrics_path,
                max_tokens=128,
                temperature=0.0,
                timeout_s=1.0,
                config_path=None,
                env_file=None,
            )
            client = OpenAIChatClient(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
                timeout_s=config.timeout_s,
            )
            engine = VisualEngine(
                config=config,
                client=client,
                metrics=MetricsRecorder(metrics_path),
                dry_run=True,
            )

            with contextlib.redirect_stderr(io.StringIO()):
                result = engine.translate_ocr_text("Visible text.")

            self.assertIn("Visible text.", result.translated_text)
            record = metrics_path.read_text(encoding="utf-8")

        self.assertIn('"phase": 2', record)
        self.assertIn('"engine": "visual"', record)


if __name__ == "__main__":
    unittest.main()
