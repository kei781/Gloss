import contextlib
import io
import unittest

from gloss.text.cli import main


class TextCliTest(unittest.TestCase):
    def test_rejects_conflicting_url_ssl_options(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code = main(
                [
                    "--dry-run",
                    "--url",
                    "https://example.com",
                    "--url-ca-bundle",
                    "corp.pem",
                    "--url-insecure-skip-verify",
                ]
            )

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
