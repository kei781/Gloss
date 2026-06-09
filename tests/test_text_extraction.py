from pathlib import Path
import tempfile
import unittest

from gloss.text.engine import split_text_blocks
from gloss.text.extractors import (
    ExtractionError,
    build_url_ssl_context,
    extract_readable_text_from_html,
    extract_text_source,
)


class TextExtractionTest(unittest.TestCase):
    def test_extract_html_skips_navigation(self) -> None:
        html = """
        <html>
          <head><title>Story</title><style>.x { color: red }</style></head>
          <body>
            <nav>Home Menu Login</nav>
            <main>
              <h1>Chapter 1</h1>
              <p>The old town slept under moonlight.</p>
              <p>A traveler opened the blue door.</p>
            </main>
            <script>alert('skip')</script>
          </body>
        </html>
        """
        title, text = extract_readable_text_from_html(html)

        self.assertEqual(title, "Story")
        self.assertIn("The old town slept under moonlight.", text)
        self.assertIn("A traveler opened the blue door.", text)
        self.assertNotIn("Home Menu Login", text)
        self.assertNotIn("alert", text)

    def test_extract_file_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.txt"
            path.write_text("Line one.\n\nLine two.", encoding="utf-8")

            document = extract_text_source(file=path)

        self.assertEqual(document.source_kind, "file")
        self.assertEqual(document.text, "Line one.\n\nLine two.")

    def test_extract_file_text_supports_cp949(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.txt"
            path.write_bytes("\uc548\ub155\ud558\uc138\uc694.".encode("cp949"))

            document = extract_text_source(file=path)

        self.assertEqual(document.text, "\uc548\ub155\ud558\uc138\uc694.")

    def test_extract_file_text_reports_encoding_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.txt"
            path.write_bytes(bytes([0xFF, 0xFF]))

            with self.assertRaises(ExtractionError):
                extract_text_source(file=path)

    def test_url_ssl_context_can_skip_verification(self) -> None:
        context = build_url_ssl_context(verify_ssl=False)

        self.assertIsNotNone(context)
        self.assertFalse(context.check_hostname)

    def test_url_ssl_context_reports_missing_ca_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.pem"

            with self.assertRaises(ExtractionError):
                build_url_ssl_context(ca_bundle=path)

    def test_split_text_blocks_respects_limit(self) -> None:
        text = "A" * 20 + "\n\n" + "B" * 20 + "\n\n" + "C" * 20
        blocks = list(split_text_blocks(text, max_chars=45))

        self.assertEqual(len(blocks), 2)
        self.assertLessEqual(len(blocks[0]), 45)
        self.assertLessEqual(len(blocks[1]), 45)


if __name__ == "__main__":
    unittest.main()
