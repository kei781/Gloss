import json
import unittest

from gloss.backend.openai_client import OpenAIChatClient


class FakeResponse:
    def __init__(self, *, body: bytes = b"", lines: list[bytes] | None = None):
        self.body = body
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body

    def __iter__(self):
        return iter(self.lines)


class StubClient(OpenAIChatClient):
    def __init__(self, response: FakeResponse):
        super().__init__(
            base_url="http://127.0.0.1:11435/v1",
            api_key="local",
            model="test-model",
            timeout_s=1.0,
        )
        self.response = response

    def _post_json(self, payload):
        return self.response


class OpenAIClientTest(unittest.TestCase):
    def test_non_stream_length_finish_reason_marks_generation_truncated(self) -> None:
        response = FakeResponse(
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "partial"},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"completion_tokens": 4, "prompt_tokens": 3},
                }
            ).encode("utf-8")
        )

        result = StubClient(response).complete(
            messages=[{"role": "user", "content": "source"}],
            max_tokens=4,
            temperature=0.0,
            stream=False,
        )

        self.assertEqual(result.finish_reason, "length")
        self.assertTrue(result.truncated)

    def test_stream_length_finish_reason_marks_generation_truncated(self) -> None:
        response = FakeResponse(
            lines=[
                b'data: {"choices":[{"delta":{"content":"part"},"finish_reason":null}]}\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"length"}],"usage":{"completion_tokens":1}}\n',
                b"data: [DONE]\n",
            ]
        )

        result = StubClient(response).complete(
            messages=[{"role": "user", "content": "source"}],
            max_tokens=1,
            temperature=0.0,
            stream=True,
        )

        self.assertEqual(result.text, "part")
        self.assertEqual(result.finish_reason, "length")
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()
