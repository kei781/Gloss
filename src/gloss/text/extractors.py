from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Literal
from urllib.error import HTTPError, URLError
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
        data = read_text_file(file)
        if file.suffix.lower() in {".html", ".htm"}:
            title, body = extract_readable_text_from_html(data)
        else:
            title, body = None, normalize_text(data)
        return ExtractedDocument("file", str(file), title, body)

    assert url is not None
    html = fetch_url(url, timeout_s=timeout_s)
    title, body = extract_readable_text_from_html(html)
    return ExtractedDocument("url", url, title, body)


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


def fetch_url(url: str, timeout_s: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Gloss/0.1 Phase1 TextEngine",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ExtractionError(f"URL fetch failed: {exc}") from exc


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
