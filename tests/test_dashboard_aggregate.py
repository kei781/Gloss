import json
from pathlib import Path
import tempfile
import unittest

from gloss.dashboard.aggregate import (
    format_summary,
    load_metrics_rows,
    percentile,
    stats_of,
    summarize,
)


def _row(engine: str, phase: int, **generation):
    base_generation = {
        "ttft_s": 0.1,
        "tokens_per_second": 10.0,
        "end_to_end_tokens_per_second": 8.0,
        "elapsed_s": 2.0,
        "completion_tokens": 40,
        "prompt_tokens": 80,
        "token_count_source": "usage",
        "truncated": False,
    }
    base_generation.update(generation)
    return {
        "recordedAt": "2026-06-10T12:00:00+0900",
        "engine": engine,
        "phase": phase,
        "model": "phi-3.5-mini",
        "sourceChars": 100,
        "translatedChars": 120,
        "generation": base_generation,
    }


class AggregateTest(unittest.TestCase):
    def test_load_metrics_rows_counts_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            path.write_text(
                json.dumps(_row("text", 1)) + "\nnot json\n[1,2]\n",
                encoding="utf-8",
            )
            rows, bad_lines = load_metrics_rows([path, Path(temp_dir) / "missing.jsonl"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(bad_lines, 2)

    def test_summarize_groups_by_engine_and_phase(self) -> None:
        rows = [
            _row("text", 1),
            _row("text", 1, truncated=True, token_count_source="estimated_chars_per_token"),
            _row("visual", 2),
            _row("visual", 3),
        ]
        summary = summarize(rows, bad_lines=1)

        self.assertEqual(summary.total_requests, 4)
        self.assertEqual(summary.bad_lines, 1)
        keys = [group.key for group in summary.groups]
        self.assertEqual(keys, ["text/phase1", "visual/phase2", "visual/phase3"])

        text_group = summary.groups[0]
        self.assertEqual(text_group.requests, 2)
        self.assertEqual(text_group.truncated, 1)
        self.assertEqual(text_group.completion_tokens, 80)
        self.assertEqual(
            text_group.token_sources,
            {"usage": 1, "estimated_chars_per_token": 1},
        )
        self.assertEqual(text_group.models, {"phi-3.5-mini"})

    def test_percentile_nearest_rank(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

        self.assertEqual(percentile(values, 0.50), 5.0)
        self.assertEqual(percentile(values, 0.95), 10.0)
        self.assertEqual(percentile([42.0], 0.95), 42.0)

    def test_stats_of_empty(self) -> None:
        stats = stats_of([])

        self.assertEqual(stats.count, 0)
        self.assertIsNone(stats.avg)

    def test_rows_without_generation_counted_and_rendered(self) -> None:
        rows = [
            _row("text", 1),
            {"engine": "text", "phase": 1, "sourceChars": 10, "translatedChars": 12},
            {"engine": "text", "phase": 1, "generation": None},
        ]
        summary = summarize(rows)
        group = summary.groups[0]

        self.assertEqual(group.requests, 3)
        self.assertEqual(group.completion_tokens, 40)  # only the full row contributes
        self.assertEqual(group.ttft_values, [0.1])

        rendered = format_summary(summarize(rows[1:]))
        self.assertIn("ttft_s - | decode_tok/s - | e2e_tok/s - | elapsed_s -", rendered)
        self.assertIn("token_count_source: -", rendered)

    def test_format_summary_renders(self) -> None:
        summary = summarize([_row("text", 1)])
        text = format_summary(summary)

        self.assertIn("text/phase1", text)
        self.assertIn("requests: 1", text)
        self.assertIn("token_count_source: usage=1", text)


if __name__ == "__main__":
    unittest.main()
