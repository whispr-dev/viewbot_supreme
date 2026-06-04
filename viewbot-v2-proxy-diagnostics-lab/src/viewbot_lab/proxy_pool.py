"""
Proxy pool with EWMA latency scoring, per-proxy circuit breakers, pluggable
selection strategies, and graceful degradation when the pool depletes.

Design notes
============

ProxyState carries:
- A single EWMA latency value updated on every observation. We don't keep
  a sliding window — EWMA is cheap, memory-flat, and forgets old data
  exponentially, which is what we actually want here.
- A circuit breaker. A proxy with an open breaker is invisible to the
  strategy entirely; it isn't even passed in as a candidate.
- Cooldown timestamps for rate-limiting how often the same proxy is reused.

Scoring (the `score` property):
    score = (1000 / ewma_latency_ms)  *  success_rate_bias

where success_rate_bias is a smoothed ratio of successes to total attempts.
Higher score = more likely to be picked under a weighted strategy.

Graceful degradation:
    The pool's `choose()` returns `None` when no candidate is allowed by
    its breaker. Callers handle that — typically by waiting for breakers
    to half-open, or by falling back to a direct (no-proxy) route.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Iterable, Optional

from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from .config import PoolConfig
from .pool_strategies import SelectionStrategy, WeightedRandomStrategy, get_strategy


# ---------------------------------------------------------------------------
# ProxyState
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProxyState:
    proxy: str
    scheme: str
    ewma_latency_ms: float
    last_latency_ms: float
    anonymity_level: str
    speed_hint: str
    successes: int = 0
    failures: int = 0
    last_used: float = -1e9
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    @property
    def total_attempts(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        """
        Laplace-smoothed success rate. Returns 0.5 with zero attempts, so a
        fresh proxy doesn't get penalised before it's had a chance to prove
        itself. Asymptotically approaches the true rate as attempts grow.
        """
        return (self.successes + 1.0) / (self.total_attempts + 2.0)

    @property
    def score(self) -> float:
        if self.ewma_latency_ms <= 0:
            return 0.0
        base = 1000.0 / max(self.ewma_latency_ms, 1.0)
        # Bias spans roughly [0.5, 1.5] given Laplace smoothing.
        return base * (0.5 + self.success_rate)

    def update_ewma(self, observed_ms: float, alpha: float) -> None:
        """
        Update the EWMA with a fresh observation.

        alpha ∈ (0, 1]. Higher alpha = more weight on the new observation
        and less memory. A typical value is 0.2 — keeps a smooth signal
        while still reacting within ~5 samples to a regime change.
        """
        observed_ms = max(0.0, float(observed_ms))
        if self.ewma_latency_ms <= 0:
            self.ewma_latency_ms = observed_ms
        else:
            self.ewma_latency_ms = (alpha * observed_ms) + ((1.0 - alpha) * self.ewma_latency_ms)
        self.last_latency_ms = observed_ms

    def snapshot(self) -> dict[str, object]:
        return {
            "proxy": self.proxy,
            "scheme": self.scheme,
            "ewma_latency_ms": round(self.ewma_latency_ms, 3),
            "last_latency_ms": round(self.last_latency_ms, 3),
            "anonymity_level": self.anonymity_level,
            "speed_hint": self.speed_hint,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "score": round(self.score, 3),
            "last_used": self.last_used,
            "breaker": self.breaker.snapshot(),
        }


# ---------------------------------------------------------------------------
# ProxyPool
# ---------------------------------------------------------------------------


class ProxyPool:
    """Health-aware proxy pool for diagnostics and operational probing."""

    def __init__(
        self,
        states: Iterable[ProxyState],
        config: Optional[PoolConfig] = None,
        strategy: Optional[SelectionStrategy] = None,
        rng: Optional[Random] = None,
    ):
        self.config = config or PoolConfig()
        self._states: list[ProxyState] = list(states)
        self._strategy: SelectionStrategy = strategy or WeightedRandomStrategy()
        self._rng = rng or Random()
        self._by_proxy: dict[str, ProxyState] = {s.proxy: s for s in self._states}

    # -- factories ----------------------------------------------------------

    @classmethod
    def from_results_file(
        cls,
        path: Path,
        config: Optional[PoolConfig] = None,
        strategy_name: Optional[str] = None,
    ) -> "ProxyPool":
        """Load a pool from the JSON output of `test-proxies`."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = config or PoolConfig()
        breaker_cfg = CircuitBreakerConfig(
            failure_threshold=cfg.breaker_failure_threshold,
            reset_timeout_s=cfg.breaker_reset_timeout_s,
            half_open_max_probes=cfg.breaker_half_open_max_probes,
        )
        states: list[ProxyState] = []
        for row in payload.get("results", []):
            if not row.get("ok") or row.get("latency_ms") is None:
                continue
            latency = float(row["latency_ms"])
            states.append(
                ProxyState(
                    proxy=str(row["proxy"]),
                    scheme=str(row.get("scheme") or "unknown"),
                    ewma_latency_ms=latency,
                    last_latency_ms=latency,
                    anonymity_level=str(row.get("anonymity_level") or "unknown"),
                    speed_hint=str(row.get("speed_hint") or "unknown"),
                    breaker=CircuitBreaker(config=breaker_cfg),
                )
            )
        strategy = get_strategy(strategy_name) if strategy_name else WeightedRandomStrategy()
        return cls(states, config=cfg, strategy=strategy)

    # -- selection ----------------------------------------------------------

    def _eligible(self, now: float) -> list[ProxyState]:
        """Candidates whose breaker permits a call and whose cooldown has elapsed."""
        eligible = []
        for s in self._states:
            if not s.breaker.allow(now):
                continue
            if now - s.last_used < self.config.cooldown_seconds:
                continue
            eligible.append(s)
        return eligible

    def choose(self, now: Optional[float] = None, key: str | None = None) -> Optional[ProxyState]:
        """
        Return the next proxy to use, or None if the pool is depleted.

        `key` is consulted by sticky strategies; ignored by others.
        """
        now = time.time() if now is None else now
        candidates = self._eligible(now)
        chosen = self._strategy.select(candidates, self._rng, key=key)
        if chosen is not None:
            chosen.last_used = now
        return chosen

    # -- feedback -----------------------------------------------------------

    def report_success(self, proxy: str, latency_ms: float | None = None) -> None:
        state = self._by_proxy.get(proxy)
        if state is None:
            return
        state.successes += 1
        if latency_ms is not None:
            state.update_ewma(latency_ms, alpha=self.config.ewma_alpha)
        state.breaker.record_success()

    def report_failure(self, proxy: str, now: float | None = None) -> None:
        state = self._by_proxy.get(proxy)
        if state is None:
            return
        state.failures += 1
        state.breaker.record_failure(time.time() if now is None else now)

    # -- introspection ------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._states)

    @property
    def healthy_size(self) -> int:
        return sum(1 for s in self._states if not s.breaker.is_open)

    def stats(self) -> dict[str, object]:
        healthy = [s for s in self._states if not s.breaker.is_open]
        by_scheme: dict[str, int] = {}
        by_speed: dict[str, int] = {}
        by_anonymity: dict[str, int] = {}
        by_breaker: dict[str, int] = {}
        for state in self._states:
            by_breaker[state.breaker.state.value] = by_breaker.get(state.breaker.state.value, 0) + 1
        for state in healthy:
            by_scheme[state.scheme] = by_scheme.get(state.scheme, 0) + 1
            by_speed[state.speed_hint] = by_speed.get(state.speed_hint, 0) + 1
            by_anonymity[state.anonymity_level] = by_anonymity.get(state.anonymity_level, 0) + 1
        latencies = sorted(s.ewma_latency_ms for s in healthy if s.ewma_latency_ms > 0)
        median_latency = latencies[len(latencies) // 2] if latencies else None
        return {
            "total": len(self._states),
            "healthy": len(healthy),
            "strategy": self._strategy.name,
            "by_scheme": by_scheme,
            "by_speed": by_speed,
            "by_anonymity": by_anonymity,
            "by_breaker_state": by_breaker,
            "median_ewma_latency_ms": median_latency,
        }

    def snapshot(self) -> list[dict[str, object]]:
        return [s.snapshot() for s in self._states]
