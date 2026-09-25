from pathlib import Path
import base64
import contextlib
from importlib.util import find_spec
import io
import json
import tempfile
import unittest

from gloss.backend.openai_client import GenerationResult, OpenAIChatClient
from gloss.config import RuntimeConfig
from gloss.metrics import MetricsRecorder
from gloss.visual.engine import VisualEngine, _fit_image


class VisualEngineTest(unittest.TestCase):
    @unittest.skipUnless(find_spec("PIL"), "Pillow unavailable")
    def test_vlm_image_fit_limits_image_tokens_without_changing_aspect_ratio(self) -> None:
        from PIL import Image

        original = io.BytesIO()
        Image.new("RGB", (2048, 512), "white").save(original, format="PNG")
        fitted, source_size, sent_size = _fit_image(
            original.getvalue(), media_type="image/png", max_edge=1024,
        )
        self.assertEqual(source_size, (2048, 512))
        self.assertEqual(sent_size, (1024, 256))
        with Image.open(io.BytesIO(fitted)) as image:
            self.assertEqual(image.size, sent_size)

    @unittest.skipUnless(find_spec("PIL"), "Pillow unavailable")
    def test_vlm_image_fit_obeys_prompt_area_and_exif_orientation(self) -> None:
        from PIL import Image

        original = io.BytesIO()
        Image.new("RGB", (1000, 1000), "white").save(original, format="PNG")
        _bytes, _source, sent_size = _fit_image(
            original.getvalue(), media_type="image/png", max_edge=2048,
            max_prompt_len=256,
        )
        self.assertLessEqual(sent_size[0] * sent_size[1], (256 - 128) * 1024)

        exif = Image.Exif()
        exif[274] = 6
        rotated = io.BytesIO()
        Image.new("RGB", (20, 40), "white").save(rotated, format="JPEG", exif=exif)
        fitted, source_size, sent_size = _fit_image(
            rotated.getvalue(), media_type="image/jpeg", max_edge=1024,
        )
        self.assertEqual(source_size, (20, 40))
        self.assertEqual(sent_size, (40, 20))
        with Image.open(io.BytesIO(fitted)) as image:
            self.assertEqual(image.getexif().get(274, 1), 1)

    @unittest.skipUnless(find_spec("PIL"), "Pillow unavailable")
    def test_vlm_image_uses_data_url_without_logging_encoded_pixels(self) -> None:
        from PIL import Image

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
            Image.new("RGB", (32, 16), "white").save(image)
            image_bytes = image.read_bytes()
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
            self.assertNotIn(base64.b64encode(image_bytes).decode("ascii"), metrics_path.read_text(encoding="utf-8"))

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
