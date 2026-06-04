"""Tests for the per-proxy circuit breaker state machine."""
from __future__ import annotations

import unittest

from viewbot_lab.circuit_breaker import BreakerState, CircuitBreaker, CircuitBreakerConfig


class CircuitBreakerTests(unittest.TestCase):
    def _make(self, **kw) -> CircuitBreaker:
        cfg = CircuitBreakerConfig(
            failure_threshold=kw.pop("failure_threshold", 3),
            reset_timeout_s=kw.pop("reset_timeout_s", 10.0),
            half_open_max_probes=kw.pop("half_open_max_probes", 1),
        )
        return CircuitBreaker(config=cfg)

    def test_closed_allows_traffic(self) -> None:
        b = self._make()
        self.assertTrue(b.allow(now=0.0))
        self.assertIs(b.state, BreakerState.CLOSED)

    def test_opens_after_threshold(self) -> None:
        b = self._make(failure_threshold=3)
        b.record_failure(now=0.0)
        b.record_failure(now=1.0)
        self.assertIs(b.state, BreakerState.CLOSED)
        b.record_failure(now=2.0)
        self.assertIs(b.state, BreakerState.OPEN)
        # Once open, calls are rejected until the reset_timeout elapses.
        self.assertFalse(b.allow(now=2.0))
        self.assertFalse(b.allow(now=5.0))

    def test_open_to_half_open_after_reset_timeout(self) -> None:
        b = self._make(failure_threshold=2, reset_timeout_s=10.0)
        b.record_failure(now=0.0)
        b.record_failure(now=1.0)
        self.assertIs(b.state, BreakerState.OPEN)
        # Right at the cooldown boundary, the breaker half-opens and lets a probe through.
        self.assertTrue(b.allow(now=11.0))
        self.assertIs(b.state, BreakerState.HALF_OPEN)

    def test_half_open_success_closes_breaker(self) -> None:
        b = self._make(failure_threshold=2, reset_timeout_s=5.0)
        b.record_failure(now=0.0)
        b.record_failure(now=1.0)
        self.assertTrue(b.allow(now=6.0))  # half-open probe allowed
        b.record_success()
        self.assertIs(b.state, BreakerState.CLOSED)
        self.assertEqual(b.consecutive_failures, 0)

    def test_half_open_failure_reopens(self) -> None:
        b = self._make(failure_threshold=2, reset_timeout_s=5.0)
        b.record_failure(now=0.0)
        b.record_failure(now=1.0)
        self.assertTrue(b.allow(now=6.0))  # half-open probe allowed
        b.record_failure(now=6.0)
        self.assertIs(b.state, BreakerState.OPEN)
        # Cooldown clock restarts: at now=10 we're still inside the new cooldown
        self.assertFalse(b.allow(now=10.0))

    def test_half_open_caps_in_flight(self) -> None:
        b = self._make(failure_threshold=1, reset_timeout_s=5.0, half_open_max_probes=1)
        b.record_failure(now=0.0)
        # First call after cooldown: allowed (half-open probe)
        self.assertTrue(b.allow(now=6.0))
        # Second call before record_success/record_failure: rejected
        self.assertFalse(b.allow(now=6.0))

    def test_success_in_closed_resets_failure_count(self) -> None:
        b = self._make(failure_threshold=3)
        b.record_failure(now=0.0)
        b.record_failure(now=1.0)
        self.assertEqual(b.consecutive_failures, 2)
        b.record_success()
        self.assertEqual(b.consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
