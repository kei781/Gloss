import os
from pathlib import Path
import tempfile
import unittest

from gloss.config import load_runtime_config


class RuntimeConfigTest(unittest.TestCase):
    def test_loads_model_from_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            config = root / "config.json"
            profiles.write_text(
                """
                {
                  "default_profile": "fallback",
                  "profiles": {
                    "fallback": {
                      "runtime_model": "phi-3.5-mini",
                      "serve": { "base_url": "http://127.0.0.1:11435/v1" },
                      "measurement": { "max_tokens": 64 }
                    }
                  }
                }
                """,
                encoding="utf-8",
            )
            config.write_text(
                """
                {
                  "active_model_profile": "fallback",
                  "model_profiles_path": "profiles.json",
                  "backend": { "api_key": "local" }
                }
                """,
                encoding="utf-8",
            )

            runtime = load_runtime_config(config_path=config, env_file=root / "missing.env")

        self.assertEqual(runtime.profile, "fallback")
        self.assertEqual(runtime.model, "phi-3.5-mini")
        self.assertEqual(runtime.max_tokens, 64)
        self.assertEqual(runtime.base_url, "http://127.0.0.1:11435/v1")

    def test_env_alias_profile_override(self) -> None:
        old_value = os.environ.get("GLOSS_ACTIVE_MODEL_PROFILE")
        os.environ["GLOSS_ACTIVE_MODEL_PROFILE"] = "target"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                profiles = root / "profiles.json"
                config = root / "config.json"
                profiles.write_text(
                    """
                    {
                      "profiles": {
                        "target": {
                          "runtime_model": "qwen3-4b-instruct-2507",
                          "serve": { "base_url": "http://127.0.0.1:11435/v1" }
                        }
                      }
                    }
                    """,
                    encoding="utf-8",
                )
                config.write_text(
                    """
                    {
                      "model_profiles_path": "profiles.json",
                      "backend": { "api_key": "local" }
                    }
                    """,
                    encoding="utf-8",
                )

                runtime = load_runtime_config(config_path=config, env_file=root / "missing.env")
        finally:
            if old_value is None:
                os.environ.pop("GLOSS_ACTIVE_MODEL_PROFILE", None)
            else:
                os.environ["GLOSS_ACTIVE_MODEL_PROFILE"] = old_value

        self.assertEqual(runtime.profile, "target")
        self.assertEqual(runtime.model, "qwen3-4b-instruct-2507")


if __name__ == "__main__":
    unittest.main()
