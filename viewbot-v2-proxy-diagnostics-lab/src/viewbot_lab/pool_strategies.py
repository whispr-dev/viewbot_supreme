"""
Selection strategies for the proxy pool.

The pool keeps a list of healthy candidates; a *strategy* decides which one
to hand back on each call. Each strategy is a small stateful object so that
behaviour like round-robin cursors or sticky-session maps lives in one place
and is independently testable.

Convention: strategies return `None` only when `candidates` is empty.
They never mutate the candidate states themselves — that's the pool's job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Protocol, Sequence


class HasScore(Protocol):
    """Minimal interface a candidate must satisfy. ProxyState implements this."""

    proxy: str
    score: float
    ewma_latency_ms: float


class SelectionStrategy(Protocol):
    name: str

    def select(
        self,
        candidates: Sequence[HasScore],
        rng: Random,
        key: str | None = None,
    ) -> HasScore | None:
        ...


@dataclass(frozen=True, slots=True)
class WeightedRandomStrategy:
    """
    Pick a candidate with probability proportional to its score.

    This is the default. It spreads load across the pool while still
    favouring fast/healthy proxies. Robust against a single bad apple
    dominating the rotation.
    """

    name: str = "weighted_random"

    def select(
        self,
        candidates: Sequence[HasScore],
        rng: Random,
        key: str | None = None,
    ) -> HasScore | None:
        if not candidates:
            return None
        total = 0.0
        for c in candidates:
            total += max(0.0, c.score)
        if total <= 0.0:
            # All scores zero → fall back to uniform random so we still make
            # forward progress. The pool will rebuild scores on success.
            return rng.choice(list(candidates))
        mark = rng.uniform(0.0, total)
        cursor = 0.0
        for c in candidates:
            cursor += max(0.0, c.score)
            if mark <= cursor:
                return c
        return candidates[-1]  # floating-point tail; not reached in practice


@dataclass(slots=True)
class RoundRobinStrategy:
    """
    Strict round-robin over the candidate list.

    Useful when you want deterministic load spreading and don't care about
    differing latencies — e.g. when probing a fleet of equivalent backends.

    Note: the candidate list can shrink/grow between calls (proxies fail and
    recover). We key the cursor against the ordered list of proxy URLs so
    the rotation degrades gracefully across pool changes.
    """

    name: str = "round_robin"
    _last_proxy: str | None = None

    def select(
        self,
        candidates: Sequence[HasScore],
        rng: Random,
        key: str | None = None,
    ) -> HasScore | None:
        if not candidates:
            self._last_proxy = None
            return None
        # Find the position just after the last-served proxy, if it's still
        # in the candidate set.
        start = 0
        if self._last_proxy is not None:
            for i, c in enumerate(candidates):
                if c.proxy == self._last_proxy:
                    start = (i + 1) % len(candidates)
                    break
        chosen = candidates[start]
        self._last_proxy = chosen.proxy
        return chosen


@dataclass(slots=True)
class StickyStrategy:
    """
    Sticky-session strategy: a given `key` maps to a stable candidate as
    long as that candidate stays healthy. When the target falls out of the
    candidate set (open breaker, evicted, etc.), the strategy rolls forward
    to a new sticky assignment and remembers that.

    Useful for workflows where a sequence of requests should share a route
    (e.g. probing a backend through a single egress IP for a tracing session).

    Fallback: if `key` is None, falls back to weighted-random behaviour so
    callers don't have to special-case unkeyed traffic.
    """

    name: str = "sticky"
    _bindings: dict[str, str] = field(default_factory=dict)
    _fallback: WeightedRandomStrategy = field(default_factory=WeightedRandomStrategy)

    def select(
        self,
        candidates: Sequence[HasScore],
        rng: Random,
        key: str | None = None,
    ) -> HasScore | None:
        if not candidates:
            return None
        if key is None:
            return self._fallback.select(candidates, rng)
        bound = self._bindings.get(key)
        if bound is not None:
            for c in candidates:
                if c.proxy == bound:
                    return c
            # Bound proxy is no longer healthy — clear and re-bind below.
            self._bindings.pop(key, None)
        chosen = self._fallback.select(candidates, rng)
        if chosen is not None:
            self._bindings[key] = chosen.proxy
        return chosen


@dataclass(frozen=True, slots=True)
class LowestLatencyStrategy:
    """
    Always pick the candidate with the lowest EWMA latency.

    Greedy and not load-balancing — if the fastest proxy stays fastest,
    it keeps being picked. Pair this with a circuit breaker if you want
    the pool to spread out under pressure.
    """

    name: str = "lowest_latency"

    def select(
        self,
        candidates: Sequence[HasScore],
        rng: Random,
        key: str | None = None,
    ) -> HasScore | None:
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.ewma_latency_ms)


def get_strategy(name: str) -> SelectionStrategy:
    """Factory for CLI/config-driven strategy selection."""
    cleaned = name.strip().lower().replace("-", "_")
    if cleaned == "weighted_random":
        return WeightedRandomStrategy()
    if cleaned == "round_robin":
        return RoundRobinStrategy()
    if cleaned == "sticky":
        return StickyStrategy()
    if cleaned == "lowest_latency":
        return LowestLatencyStrategy()
    raise KeyError(
        f"unknown strategy {name!r}; known: "
        "weighted_random, round_robin, sticky, lowest_latency"
    )
