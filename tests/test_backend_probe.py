import io
import json
import unittest
from urllib.error import URLError

from gloss.backend.probe import probe_backend


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class ProbeBackendTest(unittest.TestCase):
    def test_up_with_models(self) -> None:
        payload = json.dumps(
            {"data": [{"id": "phi-3.5-mini"}, {"id": "qwen3-4b"}, {"noid": 1}]}
        ).encode("utf-8")
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return _FakeResponse(payload)

        status = probe_backend("http://127.0.0.1:11435/v1/", opener=opener)

        self.assertTrue(status.up)
        self.assertEqual(status.models, ["phi-3.5-mini", "qwen3-4b"])
        self.assertEqual(seen["url"], "http://127.0.0.1:11435/v1/models")
        self.assertEqual(seen["timeout"], 3.0)

    def test_down_on_connection_error(self) -> None:
        def opener(request, timeout):
            raise URLError("connection refused")

        status = probe_backend("http://127.0.0.1:11435/v1", opener=opener)

        self.assertFalse(status.up)
        self.assertEqual(status.models, [])
        self.assertIn("connection refused", status.error)

    def test_down_on_invalid_json(self) -> None:
        def opener(request, timeout):
            return _FakeResponse(b"not json")

        status = probe_backend("http://127.0.0.1:11435/v1", opener=opener)

        self.assertFalse(status.up)

    def test_down_on_incomplete_read(self) -> None:
        from http.client import IncompleteRead

        class _Broken(_FakeResponse):
            def read(self):
                raise IncompleteRead(b"partial")

        status = probe_backend(
            "http://127.0.0.1:11435/v1", opener=lambda r, timeout: _Broken(b"")
        )

        self.assertFalse(status.up)
        self.assertIn("IncompleteRead", status.error)

    def test_down_on_non_utf8_body(self) -> None:
        status = probe_backend(
            "http://127.0.0.1:11435/v1",
            opener=lambda r, timeout: _FakeResponse(b"\xff\xfe\x00garbage"),
        )

        self.assertFalse(status.up)


if __name__ == "__main__":
    unittest.main()
