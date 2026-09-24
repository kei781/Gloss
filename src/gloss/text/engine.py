from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable

from gloss.backend.openai_client import GenerationResult, OpenAIChatClient
from gloss.config import RuntimeConfig
from gloss.log import log
from gloss.metrics import MetricsRecorder, new_request_id
from gloss.text.extractors import ExtractedDocument


SYSTEM_PROMPT = (
    "You are Gloss, an on-device translation engine. Translate the user's "
    "source text into natural Korean. Output only the translated Korean text. "
    "Do not add explanations, quotes, labels, romanization, apologies, or notes."
)


@dataclass(frozen=True)
class TranslationBlock:
    index: int
    source_text: str
    translated_text: str
    metrics: GenerationResult


@dataclass(frozen=True)
class TranslationDocument:
    title: str | None
    source: str
    source_kind: str
    blocks: list[TranslationBlock]

    @property
    def translated_text(self) -> str:
        return "\n\n".join(block.translated_text.strip() for block in self.blocks)


class TextEngine:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        client: OpenAIChatClient,
        metrics: MetricsRecorder,
        dry_run: bool = False,
    ):
        self.config = config
        self.client = client
        self.metrics = metrics
        self.dry_run = dry_run

    def translate(
        self,
        document: ExtractedDocument,
        *,
        max_chars_per_block: int = 1800,
        max_estimated_tokens_per_block: int = 480,
        stream: bool = True,
    ) -> TranslationDocument:
        blocks: list[TranslationBlock] = []
        source_blocks = list(split_text_blocks(
            document.text, max_chars_per_block,
            max_estimated_tokens=max_estimated_tokens_per_block,
        ))
        if not source_blocks:
            raise TextEngineError("No text blocks to translate.")

        log(
            "text translation started",
            source_kind=document.source_kind,
            blocks=len(source_blocks),
            model=self.config.model,
        )

        for index, source_text in enumerate(source_blocks, start=1):
            request_id = new_request_id()
            log("translating block", request_id=request_id, block=index)
            result = self._translate_block(source_text, stream=stream)
            block = TranslationBlock(
                index=index,
                source_text=source_text,
                translated_text=result.text.strip(),
                metrics=result,
            )
            blocks.append(block)
            if result.truncated:
                log(
                    "translation block truncated",
                    level="WARN",
                    request_id=request_id,
                    block=index,
                    finish_reason=result.finish_reason,
                    max_tokens=self.config.max_tokens,
                )
            self.metrics.write(
                {
                    "requestId": request_id,
                    "phase": 1,
                    "engine": "text",
                    "sourceKind": document.source_kind,
                    "source": document.source,
                    "title": document.title,
                    "blockIndex": index,
                    "blockCount": len(source_blocks),
                    "modelProfile": self.config.profile,
                    "model": self.config.model,
                    "backendBaseUrl": self.config.base_url,
                    "maxTokens": self.config.max_tokens,
                    "sourceChars": len(source_text),
                    "translatedChars": len(block.translated_text),
                    "generation": asdict(result),
                }
            )
        log("text translation completed", blocks=len(blocks))
        return TranslationDocument(
            title=document.title,
            source=document.source,
            source_kind=document.source_kind,
            blocks=blocks,
        )

    def _translate_block(self, source_text: str, *, stream: bool) -> GenerationResult:
        if self.dry_run:
            return GenerationResult(
                text=f"[DRY RUN]\n{source_text}",
                elapsed_s=0.0,
                ttft_s=None,
                decode_window_s=None,
                completion_tokens=0,
                prompt_tokens=None,
                token_count_source="dry_run",
                tokens_per_second=None,
                end_to_end_tokens_per_second=None,
                chunks=0,
                usage=None,
                finish_reason=None,
                truncated=False,
            )

        return self.client.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": source_text},
            ],
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            stream=stream,
        )


class TextEngineError(RuntimeError):
    pass


def split_text_blocks(
    text: str, max_chars: int, *, max_estimated_tokens: int | None = None
) -> Iterable[str]:
    if max_chars < 1 or (max_estimated_tokens is not None and max_estimated_tokens < 32):
        raise ValueError("Block limits must be positive; token budget must be at least 32.")
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    current: list[str] = []
    current_len = 0

    def fits(value: str) -> bool:
        return len(value) <= max_chars and (
            max_estimated_tokens is None
            or estimate_source_tokens(value) <= max_estimated_tokens
        )

    for paragraph in paragraphs:
        if not fits(paragraph):
            if current:
                yield "\n\n".join(current)
                current = []
                current_len = 0
            yield from _split_long_paragraph(paragraph, max_chars, fits)
            continue

        next_len = current_len + len(paragraph) + (2 if current else 0)
        candidate = "\n\n".join([*current, paragraph])
        if current and (next_len > max_chars or not fits(candidate)):
            yield "\n\n".join(current)
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = next_len

    if current:
        yield "\n\n".join(current)


def _split_long_paragraph(paragraph: str, max_chars: int, fits) -> Iterable[str]:
    sentences = paragraph.replace("。", "。\n").replace(". ", ".\n").splitlines()
    chunk: list[str] = []
    chunk_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not fits(sentence):
            if chunk:
                yield " ".join(chunk)
                chunk = []
                chunk_len = 0
            remaining = sentence
            while remaining:
                low, high = 1, min(len(remaining), max_chars)
                cut = 1
                while low <= high:
                    middle = (low + high) // 2
                    if fits(remaining[:middle]):
                        cut = middle
                        low = middle + 1
                    else:
                        high = middle - 1
                piece = remaining[:cut].strip()
                if piece:
                    yield piece
                remaining = remaining[cut:].lstrip()
            continue
        next_len = chunk_len + len(sentence) + (1 if chunk else 0)
        if chunk and (next_len > max_chars or not fits(" ".join([*chunk, sentence]))):
            yield " ".join(chunk)
            chunk = [sentence]
            chunk_len = len(sentence)
        else:
            chunk.append(sentence)
            chunk_len = next_len
    if chunk:
        yield " ".join(chunk)


def estimate_source_tokens(text: str) -> int:
    """Conservative budget for mixed ASCII and CJK without loading a tokenizer."""
    return math.ceil(sum(1.5 if ord(char) > 127 else 1 / 3 for char in text))
