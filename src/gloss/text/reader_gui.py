"""Small desktop reader over the same extraction and translation path as gloss-text."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sys

from gloss.backend.openai_client import OpenAIChatClient
from gloss.config import load_runtime_config
from gloss.metrics import MetricsRecorder
from gloss.text.engine import TextEngine
from gloss.text.extractors import ExtractedDocument, extract_text_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open the Gloss desktop text reader.")
    parser.add_argument("--profile")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", help="Initial URL to load.")
    parser.add_argument("--file", type=Path, help="Initial file to load.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.url and args.file:
        raise SystemExit("Use --url or --file, not both.")
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import (
            QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
            QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
            QSpinBox, QSplitter, QVBoxLayout, QWidget,
        )
    except ImportError:
        print("Desktop reader requires: pip install -e '.[gui]'", file=sys.stderr)
        return 1

    try:
        config = load_runtime_config(
            config_path=args.config, env_file=args.env_file, profile=args.profile,
            model=args.model, base_url=args.base_url, metrics_path=args.metrics,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle("Gloss · Text Reader")
    window.resize(1180, 760)
    root = QWidget()
    layout = QVBoxLayout(root)

    source_row = QHBoxLayout()
    mode = QComboBox()
    mode.addItems(["Text", "URL", "File"])
    location = QLineEdit()
    location.setPlaceholderText("URL or file path; paste text in the left pane")
    browse = QPushButton("Browse…")
    load = QPushButton("Load")
    source_row.addWidget(mode)
    source_row.addWidget(location, 1)
    source_row.addWidget(browse)
    source_row.addWidget(load)
    layout.addLayout(source_row)

    options = QHBoxLayout()
    render_js = QCheckBox("Render JavaScript")
    options.addWidget(render_js)
    options.addWidget(QLabel("Additional pages"))
    next_pages = QSpinBox()
    next_pages.setRange(0, 20)
    options.addWidget(next_pages)
    options.addStretch(1)
    options.addWidget(QLabel(f"Model: {config.model}"))
    layout.addLayout(options)

    panes = QSplitter()
    source_edit = QPlainTextEdit()
    source_edit.setPlaceholderText("Paste the source text here, or load a URL/PDF/file above.")
    translated_edit = QPlainTextEdit()
    translated_edit.setReadOnly(True)
    translated_edit.setPlaceholderText("Korean translation appears here.")
    panes.addWidget(source_edit)
    panes.addWidget(translated_edit)
    panes.setSizes([590, 590])
    layout.addWidget(panes, 1)

    action_row = QHBoxLayout()
    translate = QPushButton("Translate")
    save = QPushButton("Save translation…")
    status = QLabel("Ready")
    action_row.addWidget(translate)
    action_row.addWidget(save)
    action_row.addWidget(status, 1)
    layout.addLayout(action_row)
    window.setCentralWidget(root)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gloss-reader")
    pending: Future | None = None
    pending_action = ""
    loaded: ExtractedDocument | None = None

    def set_busy(busy: bool, label: str) -> None:
        load.setEnabled(not busy)
        translate.setEnabled(not busy)
        status.setText(label)

    def browse_file() -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            window, "Open source", "", "Text, HTML, PDF (*.txt *.html *.htm *.pdf);;All files (*)"
        )
        if chosen:
            mode.setCurrentText("File")
            location.setText(chosen)

    def load_source() -> None:
        nonlocal pending, pending_action
        selected = mode.currentText()
        if selected == "Text":
            status.setText("Paste text in the left pane.")
            return
        address = location.text().strip()
        if not address:
            status.setText("Enter a URL or choose a file.")
            return
        pending_action = "load"
        set_busy(True, "Loading source…")
        if selected == "URL":
            pending = executor.submit(
                extract_text_source, url=address, render_js=render_js.isChecked(),
                next_pages=next_pages.value(),
                timeout_s=min(config.timeout_s, 60.0),
            )
        else:
            pending = executor.submit(extract_text_source, file=Path(address))

    def translate_source() -> None:
        nonlocal pending, pending_action
        source = source_edit.toPlainText().strip()
        if not source:
            status.setText("Load or paste source text first.")
            return
        selected = mode.currentText()
        document = loaded if loaded is not None and selected != "Text" else None
        if document is None:
            document = ExtractedDocument("text", "inline", None, source)
        else:
            document = ExtractedDocument(document.source_kind, document.source, document.title, source)
        client = OpenAIChatClient(
            base_url=config.base_url, api_key=config.api_key,
            model=config.model, timeout_s=config.timeout_s,
        )
        engine = TextEngine(
            config=config, client=client, metrics=MetricsRecorder(config.metrics_path),
            dry_run=args.dry_run,
        )
        pending_action = "translate"
        set_busy(True, "Translating…")
        pending = executor.submit(engine.translate, document)

    def poll() -> None:
        nonlocal pending, loaded
        if pending is None or not pending.done():
            return
        finished = pending
        pending = None
        try:
            result = finished.result()
        except Exception as exc:
            set_busy(False, "Failed")
            QMessageBox.warning(window, "Gloss", str(exc))
            return
        if pending_action == "load":
            loaded = result
            source_edit.setPlainText(result.text)
            set_busy(False, f"Loaded {len(result.text):,} characters")
        else:
            translated_edit.setPlainText(result.translated_text)
            set_busy(False, f"Translated {len(result.blocks)} block(s)")

    def save_translation() -> None:
        content = translated_edit.toPlainText().strip()
        if not content:
            status.setText("Nothing to save yet.")
            return
        chosen, _filter = QFileDialog.getSaveFileName(window, "Save translation", "translation.md")
        if chosen:
            try:
                Path(chosen).write_text(content + "\n", encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(window, "Gloss", str(exc))
            else:
                status.setText(f"Saved {chosen}")

    browse.clicked.connect(browse_file)
    load.clicked.connect(load_source)
    translate.clicked.connect(translate_source)
    save.clicked.connect(save_translation)
    timer = QTimer(window)
    timer.timeout.connect(poll)
    timer.start(100)
    if args.url:
        mode.setCurrentText("URL")
        location.setText(args.url)
        QTimer.singleShot(0, load_source)
    elif args.file:
        mode.setCurrentText("File")
        location.setText(str(args.file))
        QTimer.singleShot(0, load_source)
    window.show()
    try:
        return app.exec()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())
