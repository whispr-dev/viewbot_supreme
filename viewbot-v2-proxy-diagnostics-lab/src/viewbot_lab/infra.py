"""
Declarative inventory of legitimate infrastructure targets.

This is your *own* network — Litehaus beacons, the FastPing.it /probe surface,
mail.whispr.dev, the mars-api endpoints, plus any one-off boxes you want
under continuous monitoring. Defining them here means the CLI command
`probe-infra` can iterate the inventory without touching code, and a stale
URL only needs editing in one place.

If you'd rather drive this from a config file later, swap `DEFAULT_INVENTORY`
for a loader that reads TOML/YAML; the rest of the lab consumes
`InfrastructureTarget` instances and doesn't care where they came from.

Convention used below:
- `name`        operator-friendly label that shows up in logs and reports
- `category`    coarse grouping for filtering (`probe-infra --category litehaus`)
- `request`     the actual HttpProbeRequest to dispatch
- `tags`        free-form metadata; e.g. region, owner, severity

Edit the URLs, expected substrings, and timing assertions to match the
current shape of each service. The placeholders below reflect the layout
described in your operational notes (Mars NUC reverse-tunneled with
`/ping` and `/beacon`, FastPing.it `/probe`, etc).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .health_probe import HttpProbeRequest


@dataclass(frozen=True, slots=True)
class InfrastructureTarget:
    name: str
    category: str
    request: HttpProbeRequest
    tags: tuple[tuple[str, str], ...] = ()

    @property
    def tag_dict(self) -> dict[str, str]:
        return dict(self.tags)


# ---------------------------------------------------------------------------
# Litehaus beacon nodes
# ---------------------------------------------------------------------------
# The beacon network exposes `/ping` (cheap liveness) and `/beacon` (the
# JSON beacon payload). We treat /ping as the primary uptime check and
# /beacon as an extra sanity probe with a tighter latency assertion since
# beacon serving should be sub-second under normal load.

LITEHAUS_NODES = (
    ("mars",      "https://mars.litehaus.example/ping",       "wales"),
    ("nyc",       "https://nyc.litehaus.example/ping",        "us-east"),
    ("london",    "https://london.litehaus.example/ping",     "eu-west"),
    ("sydney",    "https://sydney.litehaus.example/ping",     "ap-southeast"),
    ("singapore", "https://singapore.litehaus.example/ping",  "ap-southeast"),
)


def _litehaus_targets() -> list[InfrastructureTarget]:
    targets: list[InfrastructureTarget] = []
    for node_name, ping_url, region in LITEHAUS_NODES:
        targets.append(
            InfrastructureTarget(
                name=f"litehaus.{node_name}.ping",
                category="litehaus",
                request=HttpProbeRequest(
                    target=ping_url,
                    method="GET",
                    timeout_seconds=5.0,
                    max_retries=1,
                    max_latency_ms=2_000.0,
                ),
                tags=(("region", region), ("node", node_name), ("endpoint", "ping")),
            )
        )
        # /beacon = parse the JSON and assert the beacon ID-ish key is there.
        beacon_url = ping_url.rsplit("/", 1)[0] + "/beacon"
        targets.append(
            InfrastructureTarget(
                name=f"litehaus.{node_name}.beacon",
                category="litehaus",
                request=HttpProbeRequest(
                    target=beacon_url,
                    method="GET",
                    timeout_seconds=5.0,
                    max_retries=1,
                    max_latency_ms=2_000.0,
                    # Adjust this key to match the field your beacon publishes.
                    json_must_have_key="beacon_id",
                ),
                tags=(("region", region), ("node", node_name), ("endpoint", "beacon")),
            )
        )
    return targets


# ---------------------------------------------------------------------------
# FastPing.it
# ---------------------------------------------------------------------------
# The /probe surface is exposed for exactly this kind of test. We assert
# 200 + JSON + a sensible latency ceiling.

FASTPING_TARGET = InfrastructureTarget(
    name="fastping.probe",
    category="fastping",
    request=HttpProbeRequest(
        target="https://fastping.it.com/probe",
        method="GET",
        timeout_seconds=8.0,
        max_retries=2,
        max_latency_ms=3_000.0,
        json_must_have_key="status",
    ),
    tags=(("owner", "self"), ("service", "fastping")),
)


# ---------------------------------------------------------------------------
# mars-api (the helper API running alongside the Mars master node)
# ---------------------------------------------------------------------------

MARS_API_TARGET = InfrastructureTarget(
    name="mars.api.health",
    category="mars-api",
    request=HttpProbeRequest(
        target="https://mars.litehaus.example/health",
        method="GET",
        timeout_seconds=5.0,
        max_retries=1,
        max_latency_ms=1_500.0,
    ),
    tags=(("region", "wales"), ("service", "mars-api")),
)


# ---------------------------------------------------------------------------
# mail.whispr.dev — outward-facing HTTPS (e.g. webmail UI, ACME endpoints)
# ---------------------------------------------------------------------------
# This is an HTTPS reachability + cert-chain check; the body check is
# deliberately loose because the landing page content can shift.

MAIL_WHISPR_TARGET = InfrastructureTarget(
    name="mail.whispr.dev",
    category="whispr",
    request=HttpProbeRequest(
        target="https://mail.whispr.dev/",
        method="GET",
        timeout_seconds=10.0,
        max_retries=2,
        max_latency_ms=5_000.0,
        # Permit redirects to login pages (3xx) by relaxing the upper bound.
        expected_status_min=200,
        expected_status_max=399,
    ),
    tags=(("owner", "self"), ("service", "mail")),
)


# ---------------------------------------------------------------------------
# 80days.site — the Rust/Axum slot machine backend
# ---------------------------------------------------------------------------

EIGHTYDAYS_TARGET = InfrastructureTarget(
    name="80days.site",
    category="80days",
    request=HttpProbeRequest(
        target="https://80days.site/",
        method="GET",
        timeout_seconds=8.0,
        max_retries=1,
        max_latency_ms=4_000.0,
    ),
    tags=(("owner", "self"), ("service", "slot-machine")),
)


# ---------------------------------------------------------------------------
# Default inventory
# ---------------------------------------------------------------------------

DEFAULT_INVENTORY: tuple[InfrastructureTarget, ...] = tuple(
    _litehaus_targets() + [FASTPING_TARGET, MARS_API_TARGET, MAIL_WHISPR_TARGET, EIGHTYDAYS_TARGET]
)


def filter_inventory(
    inventory: Iterable[InfrastructureTarget],
    *,
    categories: Iterable[str] | None = None,
    names: Iterable[str] | None = None,
) -> list[InfrastructureTarget]:
    """Filter the inventory by category and/or explicit name list."""
    inventory_list = list(inventory)
    if categories:
        wanted = {c.strip().lower() for c in categories}
        inventory_list = [t for t in inventory_list if t.category.lower() in wanted]
    if names:
        wanted_names = {n.strip().lower() for n in names}
        inventory_list = [t for t in inventory_list if t.name.lower() in wanted_names]
    return inventory_list
