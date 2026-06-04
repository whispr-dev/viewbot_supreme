"""Tests for the latency percentile helper."""
from __future__ import annotations

import unittest

from viewbot_lab.percentiles import LatencySummary, from_sorted, summarize


class PercentileTests(unittest.TestCase):
    def test_empty(self) -> None:
        s = summarize([])
        self.assertEqual(s.count, 0)
        self.assertIsNone(s.p50_ms)
        self.assertIsNone(s.mean_ms)

    def test_single_value(self) -> None:
        s = summarize([42.0])
        self.assertEqual(s.count, 1)
        self.assertEqual(s.min_ms, 42.0)
        self.assertEqual(s.max_ms, 42.0)
        self.assertEqual(s.p50_ms, 42.0)
        self.assertEqual(s.p95_ms, 42.0)
        self.assertEqual(s.p99_ms, 42.0)
        self.assertEqual(s.mean_ms, 42.0)

    def test_nearest_rank_basic(self) -> None:
        # 1..100 sorted, p95 should be 95, p99 should be 99
        s = from_sorted([float(i) for i in range(1, 101)])
        self.assertEqual(s.count, 100)
        self.assertEqual(s.p50_ms, 50.0)
        self.assertEqual(s.p90_ms, 90.0)
        self.assertEqual(s.p95_ms, 95.0)
        self.assertEqual(s.p99_ms, 99.0)
        self.assertEqual(s.min_ms, 1.0)
        self.assertEqual(s.max_ms, 100.0)

    def test_filters_negatives_and_none(self) -> None:
        # negatives are dropped; mean computed over the remaining values
        s = summarize([10.0, -5.0, 20.0, None, 30.0])  # type: ignore[list-item]
        self.assertEqual(s.count, 3)
        self.assertEqual(s.min_ms, 10.0)
        self.assertEqual(s.max_ms, 30.0)

    def test_to_dict_has_expected_keys(self) -> None:
        s = summarize([1.0, 2.0, 3.0])
        d = s.to_dict()
        for k in ("count", "min_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms"):
            self.assertIn(k, d)

    def test_sorted_helper_does_not_resort(self) -> None:
        # If the caller passes an already-sorted list, results match.
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        s = from_sorted(values)
        self.assertEqual(s.p50_ms, 3.0)


if __name__ == "__main__":
    unittest.main()
