"""
Runner for the `probe-infra` CLI command.

Iterates the declared inventory (or a filtered subset), runs each probe
concurrently with bounded parallelism, optionally routes through a proxy
chosen by the pool, and emits per-target results plus a summary block
with overall latency percentiles.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .health_probe import HttpProbeRequest, ProbeOutcome, probe
from .infra import DEFAULT_INVENTORY, InfrastructureTarget, filter_inventory
from .io_utils import write_json_atomic
from .percentiles import summarize
from .proxy_pool import ProxyPool


@dataclass(frozen=True, slots=True)
class ProbeRunConfig:
    output_path: Path = Path("infra-probe-results.json")
    concurrency: int = 8
    categories: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    seed: int = 1337
    use_pool: bool = False
    pool_results_path: Optional[Path] = None
    pool_strategy: str = "weighted_random"

    def validate(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")


@dataclass(slots=True)
class _PerProbe:
    target_name: str
    category: str
    tags: dict[str, str]
    outcome: ProbeOutcome
    proxy_used: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "target_name": self.target_name,
            "category": self.category,
            "tags": self.tags,
            "proxy_used": self.proxy_used,
            "outcome": self.outcome.to_dict(),
        }


def _request_via_proxy(req: HttpProbeRequest, proxy_url: str) -> HttpProbeRequest:
    """Return a copy of `req` whose `proxy_url` is set."""
    return HttpProbeRequest(
        target=req.target,
        method=req.method,
        headers=req.headers,
        timeout_seconds=req.timeout_seconds,
        max_retries=req.max_retries,
        retry_backoff_base_s=req.retry_backoff_base_s,
        retry_backoff_cap_s=req.retry_backoff_cap_s,
        proxy_url=proxy_url,
        verify_ssl=req.verify_ssl,
        expected_status_min=req.expected_status_min,
        expected_status_max=req.expected_status_max,
        max_latency_ms=req.max_latency_ms,
        body_must_contain=req.body_must_contain,
        json_must_have_key=req.json_must_have_key,
        header_must_be_present=req.header_must_be_present,
        body_preview_bytes=req.body_preview_bytes,
    )


async def _run_one(
    target: InfrastructureTarget,
    pool: Optional[ProxyPool],
    rng: random.Random,
) -> _PerProbe:
    request = target.request
    proxy_used: Optional[str] = None
    if pool is not None:
        proxy_state = pool.choose(key=target.category)
        if proxy_state is not None:
            request = _request_via_proxy(request, proxy_state.proxy)
            proxy_used = proxy_state.proxy
    outcome = await probe(request, rng=rng)
    if pool is not None and proxy_used is not None:
        if outcome.ok and outcome.latency_ms is not None:
            pool.report_success(proxy_used, latency_ms=outcome.latency_ms)
        else:
            pool.report_failure(proxy_used)
    return _PerProbe(
        target_name=target.name,
        category=target.category,
        tags=target.tag_dict,
        outcome=outcome,
        proxy_used=proxy_used,
    )


async def run_probe(
    config: ProbeRunConfig,
    inventory: Iterable[InfrastructureTarget] | None = None,
) -> dict[str, object]:
    """Dispatch every selected target concurrently and return the report."""
    config.validate()
    targets = list(filter_inventory(
        inventory or DEFAULT_INVENTORY,
        categories=config.categories or None,
        names=config.names or None,
    ))
    if not targets:
        raise ValueError("no targets selected; check --category and --name filters")

    pool: Optional[ProxyPool] = None
    if config.use_pool:
        if config.pool_results_path is None or not Path(config.pool_results_path).exists():
            raise ValueError("--use-pool requires --pool-results pointing at proxy-results.json")
        pool = ProxyPool.from_results_file(
            Path(config.pool_results_path),
            strategy_name=config.pool_strategy,
        )

    rng = random.Random(config.seed)
    semaphore = asyncio.Semaphore(config.concurrency)

    async def bounded(target: InfrastructureTarget) -> _PerProbe:
        async with semaphore:
            return await _run_one(target, pool, rng)

    results = await asyncio.gather(*(bounded(t) for t in targets))

    latencies = [r.outcome.latency_ms for r in results if r.outcome.latency_ms is not None]
    summary = summarize(latencies)
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.category, {"ok": 0, "failed": 0})
        bucket["ok" if r.outcome.ok else "failed"] += 1

    payload: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_targets": len(results),
        "ok": sum(1 for r in results if r.outcome.ok),
        "failed": sum(1 for r in results if not r.outcome.ok),
        "by_category": by_category,
        "latency_summary_ms": summary.to_dict(),
        "via_proxy_pool": pool is not None,
        "proxy_pool_stats": pool.stats() if pool is not None else None,
        "results": [r.to_dict() for r in results],
    }
    write_json_atomic(config.output_path, payload)
    return payload
