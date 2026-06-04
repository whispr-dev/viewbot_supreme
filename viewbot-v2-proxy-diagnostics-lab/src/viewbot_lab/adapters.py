"""
Lab adapters.

An adapter is a small, swappable unit of work the lab runner executes per
"event". Originally the lab shipped only a local-only simulation adapter
(`LocalEchoAdapter`). This module adds real adapters that probe your own
infrastructure — Litehaus beacons, FastPing.it, mars-api, mail.whispr.dev.

Two adapter shapes coexist:

1. `SyncLabAdapter` (Protocol with `run_event` returning a dict)
   — kept for back-compat with the existing `lab_runner.run_lab` flow.
   The local-echo adapter still implements this.

2. `AsyncProbeAdapter` (Protocol with `async run_event`)
   — runs a real HTTP health probe and returns the outcome as a dict.
   These are used when the lab runner is in "probe" mode rather than
   "simulate" mode.

`get_adapter(name)` returns the right concrete instance. Names map to:
    local_echo         → simulation
    litehaus_beacon    → Litehaus /ping + /beacon
    fastping_probe     → FastPing.it /probe
    mars_api_health    → mars-api /health
    mail_whispr        → mail.whispr.dev landing/HTTPS reachability
    health_check       → caller-supplied URL via env-style override
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from .health_probe import HttpProbeRequest, probe
from .infra import (
    EIGHTYDAYS_TARGET,
    FASTPING_TARGET,
    MAIL_WHISPR_TARGET,
    MARS_API_TARGET,
    InfrastructureTarget,
    _litehaus_targets,
)
from .proxy_pool import ProxyState


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class SyncLabAdapter(Protocol):
    name: str
    is_async: bool

    def run_event(
        self,
        event_index: int,
        proxy: ProxyState | None,
        rng: random.Random,
    ) -> dict[str, object]:
        ...


@runtime_checkable
class AsyncProbeAdapter(Protocol):
    name: str
    is_async: bool

    async def run_event(
        self,
        event_index: int,
        proxy: ProxyState | None,
        rng: random.Random,
    ) -> dict[str, object]:
        ...


LabAdapter = SyncLabAdapter | AsyncProbeAdapter


# ---------------------------------------------------------------------------
# Local echo — kept for the simulation pathway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalEchoAdapter:
    """
    Local-only learning adapter that models orchestration without touching
    any external service. Useful for testing pool dynamics under controlled
    conditions and for documentation examples.
    """

    name: str = "local_echo"
    is_async: bool = False

    def run_event(
        self,
        event_index: int,
        proxy: ProxyState | None,
        rng: random.Random,
    ) -> dict[str, object]:
        proxy_label = proxy.proxy if proxy else "direct/no-proxy"
        jitter_ms = rng.randint(20, 250)
        digest = hashlib.sha256(f"{event_index}:{proxy_label}:{jitter_ms}".encode()).hexdigest()[:16]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "adapter": self.name,
            "event_index": event_index,
            "route_label": proxy_label,
            "simulated_jitter_ms": jitter_ms,
            "synthetic_event_id": digest,
            "note": "local simulation only; no external requests made",
        }


# ---------------------------------------------------------------------------
# Single-target probe adapter base
# ---------------------------------------------------------------------------


@dataclass
class SingleTargetProbeAdapter:
    """
    Async adapter that runs one health probe per event against a fixed
    infrastructure target. Optionally routes through a pool-supplied proxy.
    """

    target: InfrastructureTarget
    name: str
    is_async: bool = True

    async def run_event(
        self,
        event_index: int,
        proxy: ProxyState | None,
        rng: random.Random,
    ) -> dict[str, object]:
        request = self.target.request
        proxy_used: Optional[str] = None
        if proxy is not None:
            proxy_used = proxy.proxy
            request = HttpProbeRequest(
                target=request.target,
                method=request.method,
                headers=request.headers,
                timeout_seconds=request.timeout_seconds,
                max_retries=request.max_retries,
                retry_backoff_base_s=request.retry_backoff_base_s,
                retry_backoff_cap_s=request.retry_backoff_cap_s,
                proxy_url=proxy.proxy,
                verify_ssl=request.verify_ssl,
                expected_status_min=request.expected_status_min,
                expected_status_max=request.expected_status_max,
                max_latency_ms=request.max_latency_ms,
                body_must_contain=request.body_must_contain,
                json_must_have_key=request.json_must_have_key,
                header_must_be_present=request.header_must_be_present,
                body_preview_bytes=request.body_preview_bytes,
            )
        outcome = await probe(request, rng=rng)
        return {
            "adapter": self.name,
            "event_index": event_index,
            "target_name": self.target.name,
            "category": self.target.category,
            "proxy_used": proxy_used,
            "outcome": outcome.to_dict(),
        }


# ---------------------------------------------------------------------------
# Litehaus fleet — sweep all five nodes per event
# ---------------------------------------------------------------------------


@dataclass
class LitehausFleetAdapter:
    """
    On each event, probe /ping on every Litehaus beacon node in the fleet.
    Reports fleet-wide health: one event ≈ one observability tick.

    This is the per-event analogue of a `probe-infra --category litehaus`
    sweep, useful when the lab runner is being used as a long-running
    monitor rather than a one-shot snapshot.
    """

    name: str = "litehaus_fleet"
    is_async: bool = True
    _ping_targets: list[InfrastructureTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._ping_targets:
            # Only keep the /ping checks per fleet sweep — /beacon is heavier
            # and best run from `probe-infra` rather than per-event.
            self._ping_targets = [t for t in _litehaus_targets() if t.tag_dict.get("endpoint") == "ping"]

    async def run_event(
        self,
        event_index: int,
        proxy: ProxyState | None,
        rng: random.Random,
    ) -> dict[str, object]:
        coros = []
        for target in self._ping_targets:
            request = target.request
            if proxy is not None:
                request = HttpProbeRequest(
                    target=request.target,
                    method=request.method,
                    headers=request.headers,
                    timeout_seconds=request.timeout_seconds,
                    max_retries=request.max_retries,
                    proxy_url=proxy.proxy,
                    verify_ssl=request.verify_ssl,
                    expected_status_min=request.expected_status_min,
                    expected_status_max=request.expected_status_max,
                    max_latency_ms=request.max_latency_ms,
                )
            coros.append(probe(request, rng=rng))
        outcomes = await asyncio.gather(*coros)
        ok = sum(1 for o in outcomes if o.ok)
        return {
            "adapter": self.name,
            "event_index": event_index,
            "proxy_used": proxy.proxy if proxy else None,
            "fleet_size": len(outcomes),
            "ok": ok,
            "failed": len(outcomes) - ok,
            "outcomes": [
                {"target": t.name, "outcome": o.to_dict()}
                for t, o in zip(self._ping_targets, outcomes)
            ],
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_adapter(name: str) -> LabAdapter:
    cleaned = name.strip().lower().replace("-", "_")
    if cleaned == "local_echo":
        return LocalEchoAdapter()
    if cleaned == "litehaus_beacon":
        # Single-target variant: probe the Mars node specifically.
        ping_targets = [t for t in _litehaus_targets() if t.tag_dict.get("endpoint") == "ping"]
        # Default to the first listed node, which by convention is Mars.
        target = next((t for t in ping_targets if "mars" in t.name), ping_targets[0])
        return SingleTargetProbeAdapter(target=target, name="litehaus_beacon")
    if cleaned == "litehaus_fleet":
        return LitehausFleetAdapter()
    if cleaned == "fastping_probe":
        return SingleTargetProbeAdapter(target=FASTPING_TARGET, name="fastping_probe")
    if cleaned == "mars_api_health":
        return SingleTargetProbeAdapter(target=MARS_API_TARGET, name="mars_api_health")
    if cleaned == "mail_whispr":
        return SingleTargetProbeAdapter(target=MAIL_WHISPR_TARGET, name="mail_whispr")
    if cleaned == "eightydays":
        return SingleTargetProbeAdapter(target=EIGHTYDAYS_TARGET, name="eightydays")
    raise KeyError(
        f"unknown lab adapter: {name!r}. Known: "
        "local_echo, litehaus_beacon, litehaus_fleet, fastping_probe, "
        "mars_api_health, mail_whispr, eightydays"
    )


KNOWN_ADAPTERS: tuple[str, ...] = (
    "local_echo",
    "litehaus_beacon",
    "litehaus_fleet",
    "fastping_probe",
    "mars_api_health",
    "mail_whispr",
    "eightydays",
)
