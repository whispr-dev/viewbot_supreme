"""
Configuration dataclasses for the lab.

Everything mutable about how a command runs is funnelled through one of
these frozen dataclasses. The CLI builds them from argparse output; tests
construct them directly.

Keeping these immutable + explicitly typed means it's easy to:
- log the effective config of any run for reproducibility,
- diff two runs by serialising their configs,
- thread a config through async fan-out without worrying about mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_ALLOWED_REFLECTOR_HOSTS = (
    "httpbin.org",
    "postman-echo.com",
    "fastping.it.com",
    "127.0.0.1",
    "localhost",
)


# ---------------------------------------------------------------------------
# Proxy tester
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyTesterConfig:
    input_path: Path
    working_output: Path = Path("working-proxies.txt")
    json_output: Path = Path("proxy-results.json")
    target_url: str = "https://httpbin.org/anything"
    concurrency: int = 50
    timeout_seconds: float = 10.0

    # Parser behaviour
    missing_scheme_mode: str = "expand"  # reject | assume | expand
    assumed_scheme: str = "http"
    expansion_schemes: tuple[str, ...] = ("http", "https", "socks4", "socks5")

    # Diagnostics behaviour
    detect_origin: bool = False
    allowed_reflector_hosts: tuple[str, ...] = DEFAULT_ALLOWED_REFLECTOR_HOSTS

    # Retry budget per-proxy
    max_retries: int = 0
    retry_backoff_base_s: float = 0.25
    retry_backoff_cap_s: float = 2.0

    # RNG seed for deterministic backoff jitter
    seed: int = 1337

    def validate(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be > 0")
        if self.missing_scheme_mode not in {"reject", "assume", "expand"}:
            raise ValueError("missing_scheme_mode must be reject, assume, or expand")
        if self.assumed_scheme not in {"http", "https", "socks4", "socks5"}:
            raise ValueError("assumed_scheme must be http, https, socks4, or socks5")
        bad = set(self.expansion_schemes) - {"http", "https", "socks4", "socks5"}
        if bad:
            raise ValueError(f"unsupported expansion schemes: {sorted(bad)}")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.retry_backoff_base_s < 0:
            raise ValueError("retry_backoff_base_s must be >= 0")
        if self.retry_backoff_cap_s < self.retry_backoff_base_s:
            raise ValueError("retry_backoff_cap_s must be >= retry_backoff_base_s")


# ---------------------------------------------------------------------------
# Proxy pool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolConfig:
    # Per-call rate-limit so the same proxy isn't hammered.
    cooldown_seconds: float = 10.0

    # EWMA smoothing factor; alpha in (0, 1]. Higher = more weight on the
    # most recent observation (faster reaction; less smoothing).
    ewma_alpha: float = 0.2

    # Circuit breaker settings (mirrored into CircuitBreakerConfig).
    breaker_failure_threshold: int = 5
    breaker_reset_timeout_s: float = 30.0
    breaker_half_open_max_probes: int = 1

    def validate(self) -> None:
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        if not (0.0 < self.ewma_alpha <= 1.0):
            raise ValueError("ewma_alpha must be in (0, 1]")
        if self.breaker_failure_threshold < 1:
            raise ValueError("breaker_failure_threshold must be >= 1")
        if self.breaker_reset_timeout_s <= 0:
            raise ValueError("breaker_reset_timeout_s must be > 0")
        if self.breaker_half_open_max_probes < 1:
            raise ValueError("breaker_half_open_max_probes must be >= 1")


# ---------------------------------------------------------------------------
# Lab run (sim / orchestration)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabRunConfig:
    events: int = 20
    input_results: Path = Path("proxy-results.json")
    output_jsonl: Path = Path("lab-events.jsonl")
    seed: int = 1337
    adapter_name: str = "local_echo"
    strategy_name: str = "weighted_random"
    sticky_key: Optional[str] = None
    pool: PoolConfig = field(default_factory=PoolConfig)

    def validate(self) -> None:
        if self.events < 1:
            raise ValueError("events must be >= 1")
        self.pool.validate()


# ---------------------------------------------------------------------------
# Probe-infra
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeInfraConfig:
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
        if self.use_pool and self.pool_results_path is None:
            raise ValueError("use_pool=True requires pool_results_path")
