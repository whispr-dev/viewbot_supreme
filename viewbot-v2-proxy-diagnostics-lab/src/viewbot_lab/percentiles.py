"""
Latency aggregation helpers.

Why a hand-rolled implementation rather than `statistics.quantiles`?
- `statistics.quantiles` uses interpolation methods that are appropriate for
  continuous distributions but surprising for latency reporting, where people
  expect "p95 = the 95th-percentile observed value, nearest-rank".
- We also want a single function that returns the conventional bundle
  (count, min, p50, p90, p95, p99, max, mean) in one pass so it slots
  straight into the JSON output without churn.
- No numpy dependency keeps the lab importable in minimal environments.

This module is intentionally allocation-light: we sort the input list once
and then index into it. Callers that need to call this repeatedly on the
same dataset should sort once themselves and use `from_sorted`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Conventional latency summary block used in JSON output."""

    count: int
    min_ms: float | None
    p50_ms: float | None
    p90_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float | None
    mean_ms: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "min_ms": self.min_ms,
            "p50_ms": self.p50_ms,
            "p90_ms": self.p90_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
            "mean_ms": self.mean_ms,
        }


def _nearest_rank(sorted_values: Sequence[float], percentile: float) -> float:
    """
    Nearest-rank percentile: pick the value at position ceil(p/100 * N) - 1
    in the sorted list. Matches how p95 etc. is conventionally reported.

    Pre: sorted_values is non-empty and sorted ascending.
    """
    n = len(sorted_values)
    # ceil(p/100 * n) without importing math.ceil — integer arithmetic only
    # so we don't pay a function call cost per percentile.
    rank = -(-int(percentile * n) // 100)  # equivalent to ceil(percentile * n / 100)
    rank = max(1, min(rank, n))  # clamp into [1, n]
    return float(sorted_values[rank - 1])


def from_sorted(sorted_values: Sequence[float]) -> LatencySummary:
    """Build a LatencySummary from a list that the caller has already sorted."""
    n = len(sorted_values)
    if n == 0:
        return LatencySummary(0, None, None, None, None, None, None, None)
    total = 0.0
    for v in sorted_values:
        total += v
    return LatencySummary(
        count=n,
        min_ms=round(float(sorted_values[0]), 3),
        p50_ms=round(_nearest_rank(sorted_values, 50), 3),
        p90_ms=round(_nearest_rank(sorted_values, 90), 3),
        p95_ms=round(_nearest_rank(sorted_values, 95), 3),
        p99_ms=round(_nearest_rank(sorted_values, 99), 3),
        max_ms=round(float(sorted_values[-1]), 3),
        mean_ms=round(total / n, 3),
    )


def summarize(values: Iterable[float]) -> LatencySummary:
    """Build a LatencySummary from any iterable of latency observations."""
    cleaned = [float(v) for v in values if v is not None and v >= 0.0]
    cleaned.sort()
    return from_sorted(cleaned)
