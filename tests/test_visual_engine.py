from pathlib import Path
import base64
import contextlib
import io
import json
import tempfile
import unittest

from gloss.backend.openai_client import GenerationResult, OpenAIChatClient
from gloss.config import RuntimeConfig
from gloss.metrics import MetricsRecorder
from gloss.visual.engine import VisualEngine


class VisualEngineTest(unittest.TestCase):
    def test_vlm_image_uses_data_url_and_records_only_metadata(self) -> None:
        class FakeClient:
            def complete(self, **kwargs):
                self.messages = kwargs["messages"]
                return GenerationResult(
                    text="안녕하세요", elapsed_s=0.1, ttft_s=0.05,
                    decode_window_s=0.05, completion_tokens=3, prompt_tokens=20,
                    token_count_source="usage", tokens_per_second=60.0,
                    end_to_end_tokens_per_second=30.0, chunks=1, usage=None,
                    finish_reason="stop", truncated=False,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "dialog.png"
            image_bytes = b"\x89PNG\r\n\x1a\nimage payload"
            image.write_bytes(image_bytes)
            metrics_path = root / "visual.jsonl"
            config = RuntimeConfig(
                profile="qwen3-vl-4b", model="qwen3-vl-4b",
                base_url="http://127.0.0.1:8000/v3", api_key="local",
                metrics_path=metrics_path, max_tokens=128, temperature=0.0,
                timeout_s=1.0, config_path=None, env_file=None,
            )
            client = FakeClient()
            engine = VisualEngine(
                config=config, client=client, metrics=MetricsRecorder(metrics_path),
            )
            with contextlib.redirect_stderr(io.StringIO()):
                result = engine.translate_image(image)

            content = client.messages[1]["content"]
            self.assertEqual(result.translated_text, "안녕하세요")
            self.assertEqual(content[1]["type"], "image_url")
            self.assertEqual(
                content[1]["image_url"]["url"],
                "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
            )
            row = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(row["inputMode"], "vlm_image")
            self.assertEqual(row["image"]["bytes"], len(image_bytes))
            self.assertNotIn("base64", metrics_path.read_text(encoding="utf-8"))

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

    def test_metrics_phase_and_input_mode_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metrics_path = root / "watch-metrics.jsonl"
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
                engine.translate_ocr_text(
                    "Visible text.",
                    input_mode="watch_ocr",
                    phase=3,
                    metrics_extra={"watch": {"iteration": 7}},
                )

            record = metrics_path.read_text(encoding="utf-8")

        self.assertIn('"phase": 3', record)
        self.assertIn('"inputMode": "watch_ocr"', record)
        self.assertIn('"iteration": 7', record)


if __name__ == "__main__":
    unittest.main()
