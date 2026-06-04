"""Tests for pool selection strategies."""
from __future__ import annotations

import random
import unittest
from dataclasses import dataclass

from viewbot_lab.pool_strategies import (
    LowestLatencyStrategy,
    RoundRobinStrategy,
    StickyStrategy,
    WeightedRandomStrategy,
    get_strategy,
)


@dataclass
class _Candidate:
    """Minimal stand-in for ProxyState satisfying the HasScore protocol."""
    proxy: str
    score: float
    ewma_latency_ms: float


class WeightedRandomTests(unittest.TestCase):
    def test_empty_returns_none(self) -> None:
        s = WeightedRandomStrategy()
        self.assertIsNone(s.select([], random.Random(1)))

    def test_higher_score_picked_more_often(self) -> None:
        s = WeightedRandomStrategy()
        rng = random.Random(42)
        candidates = [
            _Candidate("low", score=1.0, ewma_latency_ms=1000.0),
            _Candidate("high", score=9.0, ewma_latency_ms=100.0),
        ]
        counts = {"low": 0, "high": 0}
        for _ in range(2000):
            chosen = s.select(candidates, rng)
            assert chosen is not None
            counts[chosen.proxy] += 1
        # 9:1 weighting → expect high to dominate. Allow generous margin.
        self.assertGreater(counts["high"], counts["low"] * 4)

    def test_zero_total_falls_back_to_uniform(self) -> None:
        s = WeightedRandomStrategy()
        rng = random.Random(0)
        candidates = [_Candidate("a", 0.0, 0.0), _Candidate("b", 0.0, 0.0)]
        # Shouldn't blow up; should return one of the two.
        chosen = s.select(candidates, rng)
        self.assertIn(chosen.proxy, {"a", "b"})


class RoundRobinTests(unittest.TestCase):
    def test_rotates_in_order(self) -> None:
        s = RoundRobinStrategy()
        rng = random.Random(0)
        cs = [_Candidate("a", 1, 100), _Candidate("b", 1, 100), _Candidate("c", 1, 100)]
        order = [s.select(cs, rng).proxy for _ in range(6)]
        # First pick is index 0 (no cursor yet); then 1, 2, 0, 1, 2…
        self.assertEqual(order, ["a", "b", "c", "a", "b", "c"])

    def test_cursor_survives_shrinking_candidate_list(self) -> None:
        s = RoundRobinStrategy()
        rng = random.Random(0)
        cs = [_Candidate("a", 1, 100), _Candidate("b", 1, 100), _Candidate("c", 1, 100)]
        self.assertEqual(s.select(cs, rng).proxy, "a")
        self.assertEqual(s.select(cs, rng).proxy, "b")
        # Now 'b' goes unhealthy and is dropped from the candidate list.
        cs2 = [_Candidate("a", 1, 100), _Candidate("c", 1, 100)]
        # Last served was 'b' which is gone → restart at first candidate.
        # The strategy should still make forward progress without crashing.
        chosen = s.select(cs2, rng)
        self.assertIn(chosen.proxy, {"a", "c"})


class StickyTests(unittest.TestCase):
    def test_key_binds_to_one_proxy(self) -> None:
        s = StickyStrategy()
        rng = random.Random(1)
        cs = [_Candidate("a", 1, 100), _Candidate("b", 1, 100), _Candidate("c", 1, 100)]
        first = s.select(cs, rng, key="user-42").proxy
        for _ in range(20):
            self.assertEqual(s.select(cs, rng, key="user-42").proxy, first)

    def test_rebinds_when_bound_proxy_disappears(self) -> None:
        s = StickyStrategy()
        rng = random.Random(0)
        cs = [_Candidate("a", 1, 100), _Candidate("b", 1, 100)]
        first = s.select(cs, rng, key="k").proxy
        # Remove the bound proxy.
        cs2 = [c for c in cs if c.proxy != first]
        chosen = s.select(cs2, rng, key="k")
        self.assertNotEqual(chosen.proxy, first)
        # New binding should now be stable.
        self.assertEqual(s.select(cs2, rng, key="k").proxy, chosen.proxy)

    def test_no_key_falls_back_to_weighted_random(self) -> None:
        s = StickyStrategy()
        rng = random.Random(0)
        cs = [_Candidate("a", 1, 100), _Candidate("b", 1, 100)]
        # Without key, behaviour mirrors weighted random — should still return something.
        self.assertIsNotNone(s.select(cs, rng, key=None))


class LowestLatencyTests(unittest.TestCase):
    def test_picks_minimum_latency(self) -> None:
        s = LowestLatencyStrategy()
        rng = random.Random(0)
        cs = [
            _Candidate("slow", 1, 800),
            _Candidate("fast", 1, 50),
            _Candidate("medium", 1, 300),
        ]
        self.assertEqual(s.select(cs, rng).proxy, "fast")


class FactoryTests(unittest.TestCase):
    def test_known_names(self) -> None:
        self.assertEqual(get_strategy("weighted_random").name, "weighted_random")
        self.assertEqual(get_strategy("round_robin").name, "round_robin")
        self.assertEqual(get_strategy("sticky").name, "sticky")
        self.assertEqual(get_strategy("lowest_latency").name, "lowest_latency")
        # accept hyphenated as well
        self.assertEqual(get_strategy("round-robin").name, "round_robin")

    def test_unknown_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_strategy("nope")


if __name__ == "__main__":
    unittest.main()
