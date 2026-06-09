from pathlib import Path
import tempfile
import unittest

from gloss.text.engine import split_text_blocks
from gloss.text.extractors import extract_readable_text_from_html, extract_text_source


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

    def test_split_text_blocks_respects_limit(self) -> None:
        text = "A" * 20 + "\n\n" + "B" * 20 + "\n\n" + "C" * 20
        blocks = list(split_text_blocks(text, max_chars=45))

        self.assertEqual(len(blocks), 2)
        self.assertLessEqual(len(blocks[0]), 45)
        self.assertLessEqual(len(blocks[1]), 45)


if __name__ == "__main__":
    unittest.main()
