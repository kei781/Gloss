from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
import ssl
import tempfile
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urldefrag, urlparse
from urllib.request import Request, urlopen


SourceKind = Literal["text", "file", "url"]
FILE_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


@dataclass(frozen=True)
class ExtractedDocument:
    source_kind: SourceKind
    source: str
    title: str | None
    text: str


class ExtractionError(RuntimeError):
    pass


def extract_text_source(
    *,
    text: str | None = None,
    file: Path | None = None,
    url: str | None = None,
    timeout_s: float = 30.0,
    url_verify_ssl: bool = True,
    url_ca_bundle: Path | None = None,
    pdf_ocr_language: str | None = None,
    next_pages: int = 0,
    render_js: bool = False,
    js_wait_ms: int = 800,
) -> ExtractedDocument:
    provided = [value is not None for value in (text, file, url)]
    if sum(provided) != 1:
        raise ExtractionError("Provide exactly one of text, file, or url.")

    if text is not None:
        clean_text = normalize_text(text)
        return ExtractedDocument("text", "inline", None, clean_text)

    if file is not None:
        if not file.exists():
            raise ExtractionError(f"File not found: {file}")
        if file.suffix.lower() == ".pdf":
            return extract_pdf(file, ocr_language=pdf_ocr_language)
        data = read_text_file(file)
        if file.suffix.lower() in {".html", ".htm"}:
            title, body = extract_readable_text_from_html(data)
        else:
            title, body = None, normalize_text(data)
        return ExtractedDocument("file", str(file), title, body)

    assert url is not None
    if next_pages < 0 or next_pages > 20:
        raise ExtractionError("next_pages must be between 0 and 20.")
    if js_wait_ms < 0 or js_wait_ms > 10000:
        raise ExtractionError("js_wait_ms must be between 0 and 10000.")
    if render_js and url_ca_bundle is not None:
        raise ExtractionError("--url-ca-bundle is not supported with --render-js.")
    pages: list[str] = []
    visited: set[str] = set()
    current_url = url
    title: str | None = None
    for index in range(next_pages + 1):
        if current_url in visited:
            break
        visited.add(current_url)
        if render_js:
            html = fetch_url_js(
                current_url, timeout_s=timeout_s,
                verify_ssl=url_verify_ssl, wait_ms=js_wait_ms,
            )
        else:
            html = fetch_url(
                current_url, timeout_s=timeout_s, verify_ssl=url_verify_ssl,
                ca_bundle=url_ca_bundle,
            )
        page_title, body = extract_readable_text_from_html(html)
        if title is None:
            title = page_title
        pages.append(f"Page {index + 1}\n{body}" if next_pages else body)
        current_url = find_next_page_url(html, current_url)
        if current_url is None:
            break
    return ExtractedDocument("url", url, title, "\n\n".join(pages))


def fetch_url_js(
    url: str, *, timeout_s: float, verify_ssl: bool, wait_ms: int
) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as exc:
        raise ExtractionError("JS rendering requires: pip install -e '.[web]' and python -m playwright install chromium") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(ignore_https_errors=not verify_ssl)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ExtractionError(f"JS URL fetch failed: {exc}") from exc


def find_next_page_url(html: str, current_url: str) -> str | None:
    parser = NextPageParser()
    parser.feed(html)
    parser.close()
    base = urlparse(current_url)
    for href in parser.candidates:
        candidate, _fragment = urldefrag(urljoin(current_url, href))
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc == base.netloc and candidate != current_url:
            return candidate
    return None


class NextPageParser(HTMLParser):
    NEXT_LABELS = {"next", "next page", "next chapter", "다음", "다음 페이지", "다음화", "次へ", "次のページ", "次話", "›", "»"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._links: list[tuple[int, str]] = []
        self._anchor: tuple[str, bool, list[str]] | None = None

    @property
    def candidates(self) -> list[str]:
        return [href for _score, href in sorted(self._links, key=lambda item: -item[0])]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        href = attributes.get("href")
        if not href:
            return
        is_next = "next" in (attributes.get("rel") or "").lower().split()
        if tag == "link" and is_next:
            self._links.append((100, href))
        elif tag == "a":
            self._anchor = (href, is_next, [])

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._anchor is None:
            return
        href, is_next, parts = self._anchor
        label = " ".join("".join(parts).lower().split())
        if is_next or label in self.NEXT_LABELS:
            self._links.append((90 if is_next else 50, href))
        self._anchor = None


def extract_pdf(path: Path, *, ocr_language: str | None = None) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError("PDF extraction requires: pip install -e '.[pdf]'") from exc

    try:
        reader = PdfReader(path)
        pages: list[str] = []
        for index, page in enumerate(reader.pages):
            page_text = normalize_text(page.extract_text() or "")
            if not page_text:
                page_text = _ocr_pdf_page(path, index, ocr_language=ocr_language)
            if page_text:
                pages.append(f"Page {index + 1}\n{page_text}")
        if not pages:
            raise ExtractionError(f"PDF has no readable text: {path}")
        return ExtractedDocument("file", str(path), path.stem, "\n\n".join(pages))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"PDF extraction failed: {path}: {exc}") from exc


def _ocr_pdf_page(path: Path, index: int, *, ocr_language: str | None) -> str:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ExtractionError("Scanned PDF OCR requires: pip install -e '.[pdf]'") from exc
    from gloss.visual.ocr import OcrError, WindowsOcr

    with tempfile.TemporaryDirectory(prefix="gloss-pdf-") as temp_dir:
        image_path = Path(temp_dir) / f"page-{index + 1}.png"
        pdf = pdfium.PdfDocument(path)
        try:
            page = pdf[index]
            try:
                page.render(scale=2).to_pil().save(image_path)
            finally:
                page.close()
        finally:
            pdf.close()
        try:
            result = WindowsOcr(language=ocr_language).recognize(image_path)
        except (OcrError, OSError) as exc:
            raise ExtractionError(f"PDF OCR failed on page {index + 1}: {exc}") from exc
        return normalize_text(result.text)


def read_text_file(path: Path) -> str:
    last_decode_error: UnicodeDecodeError | None = None
    for encoding in FILE_TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_decode_error = exc
        except OSError as exc:
            raise ExtractionError(f"File read failed: {path}: {exc}") from exc

    encodings = ", ".join(FILE_TEXT_ENCODINGS)
    raise ExtractionError(
        f"File encoding not supported: {path}. Expected one of: {encodings}."
    ) from last_decode_error


def fetch_url(
    url: str,
    timeout_s: float,
    *,
    verify_ssl: bool = True,
    ca_bundle: Path | None = None,
) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Gloss/0.1 Phase1 TextEngine",
        },
        method="GET",
    )
    try:
        ssl_context = build_url_ssl_context(
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
        with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ExtractionError(f"URL fetch failed: {exc}") from exc


def build_url_ssl_context(
    *,
    verify_ssl: bool = True,
    ca_bundle: Path | None = None,
) -> ssl.SSLContext | None:
    if ca_bundle is not None and not ca_bundle.exists():
        raise ExtractionError(f"URL CA bundle not found: {ca_bundle}")
    if not verify_ssl:
        return ssl._create_unverified_context()
    if ca_bundle is not None:
        return ssl.create_default_context(cafile=str(ca_bundle))
    return None


def extract_readable_text_from_html(html: str) -> tuple[str | None, str]:
    parser = ReadableHTMLParser()
    parser.feed(html)
    parser.close()
    text = normalize_text("\n".join(parser.blocks))
    if not text:
        raise ExtractionError("No readable body text found.")
    return parser.title, text


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    collapsed: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen:
                collapsed.append("")
            blank_seen = True
            continue
        collapsed.append(line)
        blank_seen = False
    return "\n".join(collapsed).strip()


class ReadableHTMLParser(HTMLParser):
    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "select",
    }
    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "br",
        "li",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._title_depth = 0
        self._title_parts: list[str] = []
        self._current_parts: list[str] = []
        self.blocks: list[str] = []

    @property
    def title(self) -> str | None:
        title = normalize_text(" ".join(self._title_parts))
        return title or None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._title_depth += 1
            return
        if tag in self.BLOCK_TAGS:
            self._flush_current()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title" and self._title_depth > 0:
            self._title_depth -= 1
            return
        if tag in self.BLOCK_TAGS:
            self._flush_current()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._title_depth > 0:
            self._title_parts.append(data)
            return
        piece = data.strip()
        if piece:
            self._current_parts.append(piece)

    def close(self) -> None:
        self._flush_current()
        super().close()

    def _flush_current(self) -> None:
        text = normalize_text(" ".join(self._current_parts))
        self._current_parts = []
        if len(text) >= 2:
            self.blocks.append(text)
