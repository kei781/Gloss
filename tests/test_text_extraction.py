from pathlib import Path
from importlib.util import find_spec
import sys
import tempfile
import types
import unittest
from unittest import mock

from gloss.text.engine import estimate_source_tokens, split_text_blocks
from gloss.text.extractors import (
    ExtractionError,
    _ocr_pdf_page,
    extract_pdf,
    build_url_ssl_context,
    extract_readable_text_from_html,
    extract_text_source,
    find_next_page_url,
)


class TextExtractionTest(unittest.TestCase):
    def test_pdf_batches_blank_pages_and_keeps_page_order_when_ocr_fails(self) -> None:
        fake_reader = types.SimpleNamespace(pages=[
            mock.Mock(extract_text=lambda: "Native first"),
            mock.Mock(extract_text=lambda: ""),
            mock.Mock(extract_text=lambda: ""),
            mock.Mock(extract_text=lambda: "Native last"),
        ])
        with mock.patch.dict(sys.modules, {"pypdf": types.SimpleNamespace(PdfReader=lambda _path: fake_reader)}):
            with mock.patch("gloss.text.extractors._ocr_pdf_pages", return_value={
                1: "OCR middle", 2: ExtractionError("unreadable"),
            }) as ocr:
                document = extract_pdf(Path("sample.pdf"))
        ocr.assert_called_once_with(Path("sample.pdf"), [1, 2], ocr_language=None, cancel=None)
        self.assertEqual(document.text, "Page 1\nNative first\n\nPage 2\nOCR middle\n\nPage 4\nNative last")

    def test_next_page_url_stays_on_same_site(self) -> None:
        html = """
        <link rel="next" href="https://other.example/chapter/2">
        <a href="/chapter/2">Next page</a>
        """
        self.assertEqual(
            find_next_page_url(html, "https://story.example/chapter/1"),
            "https://story.example/chapter/2",
        )

    def test_next_page_uses_document_base_and_escapes_non_ascii(self) -> None:
        html = '<base href="/chapters/"><a rel="next" href="다음 화 2">Next</a>'
        self.assertEqual(
            find_next_page_url(html, "https://story.example/redirected/1"),
            "https://story.example/chapters/%EB%8B%A4%EC%9D%8C%20%ED%99%94%202",
        )

    def test_later_page_failure_keeps_previous_pages(self) -> None:
        html = '<main><p>First page.</p></main><a rel="next" href="/2">Next</a>'
        with mock.patch("gloss.text.extractors.fetch_url", side_effect=[
            (html, "https://story.example/redirected/1"), ExtractionError("HTTP 500"),
        ]) as fetch:
            document = extract_text_source(url="https://story.example/1", next_pages=2)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("First page.", document.text)

    def test_long_text_preserves_line_breaks_and_limits_digit_heavy_blocks(self) -> None:
        source = "First sentence.\nSecond sentence.\n" + "1234567890" * 90
        blocks = list(split_text_blocks(source, max_chars=1800, max_estimated_tokens=480))
        self.assertIn("First sentence.\nSecond sentence.", blocks[0])
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(estimate_source_tokens(block) <= 480 for block in blocks))

    def test_url_pagination_stops_on_cycle(self) -> None:
        pages = {
            "https://story.example/1": '<title>Story</title><main><p>First chapter.</p></main><a rel="next" href="/2">Next</a>',
            "https://story.example/2": '<main><p>Second chapter.</p></main><a rel="next" href="/1">Next</a>',
        }
        with mock.patch("gloss.text.extractors.fetch_url", side_effect=lambda url, **_kw: pages[url]) as fetch:
            document = extract_text_source(url="https://story.example/1", next_pages=5)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("First chapter.", document.text)
        self.assertIn("Second chapter.", document.text)
        self.assertEqual(document.title, "Story")

    def test_render_js_uses_browser_fetch(self) -> None:
        html = "<title>Rendered</title><main><p>Loaded with JavaScript.</p></main>"
        with mock.patch("gloss.text.extractors.fetch_url_js", return_value=html) as js_fetch:
            with mock.patch("gloss.text.extractors.fetch_url") as static_fetch:
                document = extract_text_source(
                    url="https://story.example/1", render_js=True, js_wait_ms=100,
                )
        self.assertIn("Loaded with JavaScript.", document.text)
        js_fetch.assert_called_once()
        static_fetch.assert_not_called()

    @unittest.skipUnless(find_spec("pypdf") and find_spec("reportlab"), "PDF test dependencies unavailable")
    def test_extract_text_pdf_without_ocr(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "paper.pdf"
            pdf = canvas.Canvas(str(path))
            pdf.drawString(72, 720, "The old town slept under moonlight.")
            pdf.save()
            with mock.patch("gloss.text.extractors._ocr_pdf_pages") as ocr:
                document = extract_text_source(file=path)

            self.assertIn("Page 1", document.text)
            self.assertIn("The old town slept under moonlight.", document.text)
            ocr.assert_not_called()

    @unittest.skipUnless(find_spec("pypdf") and find_spec("reportlab"), "PDF test dependencies unavailable")
    def test_scanned_pdf_page_routes_to_ocr(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan.pdf"
            pdf = canvas.Canvas(str(path))
            pdf.showPage()
            pdf.save()
            with mock.patch("gloss.text.extractors._ocr_pdf_pages", return_value={0: "Scanned dialog"}) as ocr:
                document = extract_text_source(file=path, pdf_ocr_language="en-US")

            self.assertIn("Scanned dialog", document.text)
            ocr.assert_called_once_with(path, [0], ocr_language="en-US", cancel=None)

    @unittest.skipUnless(find_spec("pypdf") and find_spec("reportlab"), "PDF test dependencies unavailable")
    def test_blank_pdf_page_does_not_hide_readable_pages(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "paper-with-blank.pdf"
            pdf = canvas.Canvas(str(path))
            pdf.drawString(72, 720, "A readable first page.")
            pdf.showPage()
            pdf.showPage()
            pdf.save()
            with mock.patch("gloss.text.extractors._ocr_pdf_pages", return_value={1: ""}) as ocr:
                document = extract_text_source(file=path)

        self.assertIn("A readable first page.", document.text)
        ocr.assert_called_once_with(path, [1], ocr_language=None, cancel=None)

    @unittest.skipUnless(find_spec("pypdfium2") and find_spec("reportlab"), "PDF rendering dependencies unavailable")
    def test_scanned_pdf_page_is_rendered_before_ocr(self) -> None:
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan.pdf"
            pdf = canvas.Canvas(str(path))
            pdf.showPage()
            pdf.save()

            def recognize(image_path):
                self.assertTrue(image_path.is_file())
                self.assertGreater(image_path.stat().st_size, 0)
                return mock.Mock(text="Screen text")

            with mock.patch("gloss.visual.ocr.WindowsOcr") as ocr_class:
                ocr_class.return_value.recognize_many.side_effect = lambda paths: [recognize(paths[0])]
                text = _ocr_pdf_page(path, 0, ocr_language="en-US")
            self.assertEqual(text, "Screen text")

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

    def test_split_text_blocks_respects_npu_prompt_budget_for_cjk(self) -> None:
        source = "日" * 900
        blocks = list(split_text_blocks(
            source, max_chars=1800, max_estimated_tokens=480,
        ))
        self.assertGreater(len(blocks), 1)
        self.assertEqual("".join(blocks), source)
        self.assertTrue(all(estimate_source_tokens(block) <= 480 for block in blocks))


if __name__ == "__main__":
    unittest.main()
