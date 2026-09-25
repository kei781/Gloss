from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
import re
import ssl
import tempfile
from threading import Event
from typing import Iterator, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urldefrag, urlparse
from urllib.request import Request, urlopen

from gloss.log import log


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
    cancel: Event | None = None,
) -> ExtractedDocument:
    _check_cancel(cancel)
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
            return extract_pdf(file, ocr_language=pdf_ocr_language, cancel=cancel)
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
    session = js_browser_session(verify_ssl=url_verify_ssl) if render_js and next_pages else nullcontext(None)
    with session as js_page:
        for index in range(next_pages + 1):
            _check_cancel(cancel)
            if current_url in visited:
                break
            try:
                if render_js:
                    result = fetch_url_js(
                        current_url, timeout_s=timeout_s,
                        verify_ssl=url_verify_ssl, wait_ms=js_wait_ms,
                        page=js_page, return_final_url=True,
                    )
                else:
                    result = fetch_url(
                        current_url, timeout_s=timeout_s, verify_ssl=url_verify_ssl,
                        ca_bundle=url_ca_bundle, return_final_url=True,
                    )
                html, final_url = result if isinstance(result, tuple) else (result, current_url)
                page_title, body = extract_readable_text_from_html(html)
            except ExtractionError as exc:
                if index == 0:
                    raise
                log("next page failed; keeping collected pages", level="WARN", url=current_url, page=index + 1, error=str(exc))
                break
            visited.update((current_url, final_url))
            if title is None:
                title = page_title
            pages.append(f"Page {index + 1}\n{body}" if next_pages else body)
            if index == next_pages:
                break
            current_url = find_next_page_url(html, final_url)
            if current_url is None:
                break
    return ExtractedDocument("url", url, title, "\n\n".join(pages))


@contextmanager
def js_browser_session(*, verify_ssl: bool) -> Iterator[object]:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as exc:
        raise ExtractionError("JS rendering requires: pip install -e '.[web]' and python -m playwright install chromium") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(ignore_https_errors=not verify_ssl)
                yield context.new_page()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ExtractionError(f"JS URL fetch failed: {exc}") from exc


def fetch_url_js(
    url: str, *, timeout_s: float, verify_ssl: bool, wait_ms: int,
    page: object | None = None, return_final_url: bool = False,
) -> str | tuple[str, str]:
    if page is None:
        with js_browser_session(verify_ssl=verify_ssl) as browser_page:
            return fetch_url_js(
                url, timeout_s=timeout_s, verify_ssl=verify_ssl,
                wait_ms=wait_ms, page=browser_page, return_final_url=return_final_url,
            )
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
        if response is not None and response.status >= 400:
            raise ExtractionError(f"JS URL fetch failed: HTTP {response.status}: {url}")
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        html = page.content()
        return (html, page.url) if return_final_url else html
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"JS URL fetch failed: {exc}") from exc


def find_next_page_url(html: str, current_url: str) -> str | None:
    parser = NextPageParser()
    parser.feed(html)
    parser.close()
    try:
        document_base = urljoin(current_url, parser.base_href) if parser.base_href else current_url
        base = urlparse(current_url)
    except ValueError:
        return None
    for href in parser.candidates:
        try:
            candidate, _fragment = urldefrag(urljoin(document_base, href))
            candidate = quote(candidate, safe=":/?#[]@!$&'()*+,;=%")
            parsed = urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.netloc == base.netloc and candidate != current_url:
            return candidate
    return None


class NextPageParser(HTMLParser):
    NEXT_LABELS = {"next", "next page", "next chapter", "다음", "다음 페이지", "다음화", "次へ", "次のページ", "次話", "›", "»"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._links: list[tuple[int, str]] = []
        self._anchor: tuple[str, bool, list[str]] | None = None
        self.base_href: str | None = None

    @property
    def candidates(self) -> list[str]:
        return [href for _score, href in sorted(self._links, key=lambda item: -item[0])]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "base" and self.base_href is None:
            self.base_href = attributes.get("href")
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


def extract_pdf(path: Path, *, ocr_language: str | None = None, cancel: Event | None = None) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError("PDF extraction requires: pip install -e '.[pdf]'") from exc

    try:
        reader = PdfReader(path)
        pages: dict[int, str] = {}
        blank_pages: list[int] = []
        first_ocr_error: ExtractionError | None = None
        for index, page in enumerate(reader.pages):
            _check_cancel(cancel)
            page_text = normalize_text(page.extract_text() or "")
            if not page_text:
                blank_pages.append(index)
            else:
                pages[index] = page_text
        if blank_pages:
            try:
                ocr_pages = _ocr_pdf_pages(path, blank_pages, ocr_language=ocr_language, cancel=cancel)
            except ExtractionError as exc:
                ocr_pages = {index: exc for index in blank_pages}
            for index, result in ocr_pages.items():
                _check_cancel(cancel)
                if isinstance(result, ExtractionError):
                    first_ocr_error = first_ocr_error or result
                    log("PDF page OCR failed; keeping other pages", level="WARN", page=index + 1, error=str(result))
                elif result:
                    pages[index] = result
        if not pages:
            if first_ocr_error is not None:
                raise first_ocr_error
            raise ExtractionError(f"PDF has no readable text: {path}")
        ordered_pages = [f"Page {index + 1}\n{pages[index]}" for index in sorted(pages)]
        return ExtractedDocument("file", str(path), path.stem, "\n\n".join(ordered_pages))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"PDF extraction failed: {path}: {exc}") from exc


def _ocr_pdf_page(path: Path, index: int, *, ocr_language: str | None) -> str:
    result = _ocr_pdf_pages(path, [index], ocr_language=ocr_language)[index]
    if isinstance(result, ExtractionError):
        raise result
    return result


def _ocr_pdf_pages(
    path: Path, indices: list[int], *, ocr_language: str | None,
    cancel: Event | None = None,
) -> dict[int, str | ExtractionError]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ExtractionError("Scanned PDF OCR requires: pip install -e '.[pdf]'") from exc
    from gloss.visual.ocr import OcrError, WindowsOcr

    with tempfile.TemporaryDirectory(prefix="gloss-pdf-") as temp_dir:
        image_paths: list[Path] = []
        rendered_indices: list[int] = []
        page_results: dict[int, str | ExtractionError] = {}
        try:
            pdf = pdfium.PdfDocument(path)
        except Exception as exc:
            raise ExtractionError(f"PDF OCR could not open document: {exc}") from exc
        try:
            for index in indices:
                _check_cancel(cancel)
                image_path = Path(temp_dir) / f"page-{index + 1}.png"
                try:
                    page = pdf[index]
                    try:
                        page.render(scale=2).to_pil().save(image_path)
                    finally:
                        page.close()
                except Exception as exc:
                    page_results[index] = ExtractionError(f"PDF OCR rendering failed on page {index + 1}: {exc}")
                    continue
                image_paths.append(image_path)
                rendered_indices.append(index)
        finally:
            pdf.close()
        if not image_paths:
            return page_results
        try:
            results = WindowsOcr(language=ocr_language).recognize_many(image_paths)
        except (OcrError, OSError) as exc:
            raise ExtractionError(f"PDF OCR failed: {exc}") from exc
        for index, result in zip(rendered_indices, results, strict=True):
            page_results[index] = (
                ExtractionError(f"PDF OCR failed on page {index + 1}: {result}")
                if isinstance(result, OcrError) else normalize_text(result.text)
            )
        return page_results


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
    return_final_url: bool = False,
) -> str | tuple[str, str]:
    try:
        request = Request(
            url,
            headers={"User-Agent": "Gloss/0.1 Phase1 TextEngine"},
            method="GET",
        )
        ssl_context = build_url_ssl_context(
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
        with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
            return (html, response.geturl()) if return_final_url else html
    except (HTTPError, URLError, TimeoutError, OSError, HTTPException, ValueError, UnicodeError) as exc:
        raise ExtractionError(f"URL fetch failed: {exc}") from exc


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ExtractionError("cancelled")


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
