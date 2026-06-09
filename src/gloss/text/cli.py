from __future__ import annotations

import argparse
from pathlib import Path
import sys

from gloss.backend.openai_client import BackendError, OpenAIChatClient
from gloss.config import load_runtime_config
from gloss.logging import log
from gloss.metrics import MetricsRecorder
from gloss.text.engine import TextEngine, TextEngineError
from gloss.text.extractors import ExtractionError, extract_text_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Gloss Phase 1 text engine.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Translate an inline text block.")
    source.add_argument("--file", type=Path, help="Translate a UTF-8 text or HTML file.")
    source.add_argument("--url", help="Fetch and translate a URL.")
    parser.add_argument("--config", type=Path, help="Config JSON path.")
    parser.add_argument("--env-file", type=Path, help="Env file path.")
    parser.add_argument("--profile", help="Model profile override.")
    parser.add_argument("--model", help="Runtime model override.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL override.")
    parser.add_argument("--api-key", help="API key override.")
    parser.add_argument("--metrics", type=Path, help="Metrics JSONL output path.")
    parser.add_argument("--max-tokens", type=int, help="Max generated tokens per block.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature.")
    parser.add_argument("--timeout", type=float, help="Backend timeout seconds.")
    parser.add_argument("--max-chars-per-block", type=int, default=1800)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Skip backend call.")
    parser.add_argument("--show-source", action="store_true")
    parser.add_argument("--output", type=Path, help="Write translated text to this file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_runtime_config(
            config_path=args.config,
            env_file=args.env_file,
            profile=args.profile,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            metrics_path=args.metrics,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout_s=args.timeout,
        )
        if config.env_file:
            log("loaded env file", path=str(config.env_file))
        if config.config_path:
            log("loaded config", path=str(config.config_path))
        document = extract_text_source(
            text=args.text,
            file=args.file,
            url=args.url,
            timeout_s=min(config.timeout_s, 60.0),
        )
        log(
            "source extracted",
            source_kind=document.source_kind,
            chars=len(document.text),
            title=document.title,
        )
        client = OpenAIChatClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            timeout_s=config.timeout_s,
        )
        engine = TextEngine(
            config=config,
            client=client,
            metrics=MetricsRecorder(config.metrics_path),
            dry_run=args.dry_run,
        )
        translated = engine.translate(
            document,
            max_chars_per_block=args.max_chars_per_block,
            stream=not args.no_stream,
        )
    except (BackendError, ExtractionError, TextEngineError, ValueError) as exc:
        log(str(exc), level="ERROR")
        return 1

    output_text = render_reader_output(translated, show_source=args.show_source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
        log("wrote translation output", path=str(args.output))
    else:
        sys.stdout.write(output_text)
        if not output_text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def render_reader_output(document, *, show_source: bool) -> str:
    lines: list[str] = []
    if document.title:
        lines.append(f"# {document.title}")
        lines.append("")
    for block in document.blocks:
        if show_source:
            lines.append(f"## 원문 {block.index}")
            lines.append(block.source_text.strip())
            lines.append("")
            lines.append(f"## 번역 {block.index}")
        lines.append(block.translated_text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
