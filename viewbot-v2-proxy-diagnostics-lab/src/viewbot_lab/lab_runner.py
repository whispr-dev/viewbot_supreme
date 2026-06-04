"""
Lab orchestration runner.

A "lab run" iterates over `config.events` ticks. On each tick:
1. The pool picks a proxy (or returns None — graceful degradation).
2. The adapter runs one event and produces a dict.
3. The pool is told what happened (success/failure + observed latency).
4. The event is appended to the JSONL output.

Adapters declare themselves as sync or async via the `is_async` attribute.
Sync adapters are called directly; async adapters are awaited. This means
the same runner serves the local-only simulation use case AND the real
infrastructure-probing use case without divergent code paths.

The output JSONL is written atomically: a fail mid-run leaves the previous
file intact rather than half-overwritten.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Optional

from .adapters import AsyncProbeAdapter, LabAdapter, SyncLabAdapter, get_adapter
from .config import LabRunConfig
from .io_utils import atomic_text_writer
from .pool_strategies import get_strategy
from .proxy_pool import ProxyPool, ProxyState


def _extract_observed_latency_ms(event: dict[str, object]) -> Optional[float]:
    """
    Pull the observed latency out of an event payload so we can feed the
    pool's EWMA. Different adapters report differently:
        - SingleTargetProbeAdapter   → event["outcome"]["latency_ms"]
        - LitehausFleetAdapter       → no single latency; we don't update EWMA
        - LocalEchoAdapter           → event["simulated_jitter_ms"]
    """
    outcome = event.get("outcome")
    if isinstance(outcome, dict):
        v = outcome.get("latency_ms")
        if isinstance(v, (int, float)):
            return float(v)
    v = event.get("simulated_jitter_ms")
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _was_event_ok(event: dict[str, object]) -> bool:
    """Best-effort success flag for pool feedback."""
    outcome = event.get("outcome")
    if isinstance(outcome, dict) and "ok" in outcome:
        return bool(outcome["ok"])
    # Fleet adapter: success ≈ everything reachable.
    if "fleet_size" in event and "ok" in event:
        return bool(event["ok"]) == bool(event["fleet_size"])
    # Local-echo: always nominally OK.
    return True


async def _dispatch(
    adapter: LabAdapter,
    event_index: int,
    state: ProxyState | None,
    rng: random.Random,
) -> dict[str, object]:
    if getattr(adapter, "is_async", False):
        # AsyncProbeAdapter contract: async run_event
        return await adapter.run_event(event_index, state, rng)  # type: ignore[misc]
    # SyncLabAdapter contract: sync run_event
    return adapter.run_event(event_index, state, rng)  # type: ignore[misc]


async def run_lab(config: LabRunConfig) -> dict[str, object]:
    config.validate()
    rng = random.Random(config.seed)
    pool = ProxyPool.from_results_file(
        config.input_results,
        config.pool,
        strategy_name=config.strategy_name,
    )
    adapter: LabAdapter = get_adapter(config.adapter_name)
    events: list[dict[str, object]] = []

    with atomic_text_writer(config.output_jsonl) as handle:
        for i in range(config.events):
            # Time-shift each tick by the cooldown plus a hair, so the pool's
            # cooldown gate doesn't artificially starve us in single-process
            # runs. For real continuous monitoring you'd use wall-clock time.
            now = float(i) * (config.pool.cooldown_seconds + 0.001)
            state = pool.choose(now=now, key=config.sticky_key)

            event = await _dispatch(adapter, i, state, rng)
            events.append(event)
            handle.write(json.dumps(event, sort_keys=False) + "\n")

            if state is not None:
                if _was_event_ok(event):
                    latency = _extract_observed_latency_ms(event)
                    pool.report_success(state.proxy, latency_ms=latency)
                else:
                    pool.report_failure(state.proxy, now=now)

    return {
        "adapter": adapter.name,
        "strategy": config.strategy_name,
        "events_written": len(events),
        "output": str(config.output_jsonl),
        "pool_stats_after_run": pool.stats(),
    }
