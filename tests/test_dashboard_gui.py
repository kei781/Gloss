import unittest

from gloss.dashboard.gui import decode_sparkline


class DashboardGuiTest(unittest.TestCase):
    def test_sparkline_uses_timestamp_order_across_metric_files(self) -> None:
        rows = [
            {"recordedAt": "2026-09-24T12:00:02Z", "generation": {"tokens_per_second": 20}},
            {"recordedAt": "2026-09-24T12:00:01Z", "generation": {"tokens_per_second": 10}},
        ]
        self.assertIn("latest 20.0", decode_sparkline(rows))

    def test_sparkline_limits_valid_rates_after_filtering(self) -> None:
        rows = [{"recordedAt": f"{index:03}", "generation": {"tokens_per_second": 3}}
                for index in range(4)]
        rows += [{"recordedAt": f"{index:03}", "generation": {}}
                 for index in range(4, 20)]
        self.assertIn("latest 3.0", decode_sparkline(rows, limit=2))


if __name__ == "__main__":
    unittest.main()
