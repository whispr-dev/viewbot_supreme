"""
Command-line entry point.

Subcommands:
    inspect           — show lineage and safety policy
    normalize-proxies — clean/expand a proxy list to scheme://host:port form
    test-proxies      — concurrent diagnostic test of proxies against a
                        reflector (httpbin / postman-echo / fastping.it)
    pool-report       — summarise a working-proxy set as a health-aware pool
    run-lab           — local orchestration simulation (or async adapter sweep)
    probe-infra       — blackbox health probes against your declared
                        infrastructure inventory
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .adapters import KNOWN_ADAPTERS
from .config import (
    LabRunConfig,
    PoolConfig,
    ProbeInfraConfig,
    ProxyTesterConfig,
)
from .lab_runner import run_lab
from .pool_strategies import get_strategy as _validate_strategy  # noqa: F401  validation only
from .probe_runner import ProbeRunConfig, run_probe
from .proxy_pool import ProxyPool
from .proxy_tester import normalize_proxy_file, test_proxies
from .registry import CommandRegistry
from .safety import SafetyError


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False))


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_inspect(args: argparse.Namespace) -> int:
    _print_json(
        {
            "package": "viewbot_lab",
            "purpose": "Proxy diagnostics + blackbox-style health probing for self-owned infrastructure.",
            "subcommands": [
                "inspect",
                "normalize-proxies",
                "test-proxies",
                "pool-report",
                "run-lab",
                "probe-infra",
            ],
            "policy": [
                "Reflector targets restricted to httpbin / postman-echo / fastping.it / loopback.",
                "Platform engagement automation is not present and is intentionally not supported.",
                "Adapter inventory targets your own services (Litehaus, FastPing.it, mars-api, mail.whispr.dev, 80days.site).",
            ],
        }
    )
    return 0


def command_normalize(args: argparse.Namespace) -> int:
    schemes = _split_csv(args.expand_schemes)
    report = normalize_proxy_file(
        Path(args.input),
        Path(args.output),
        missing_scheme_mode=args.missing_scheme_mode,
        assumed_scheme=args.assume_scheme,
        expansion_schemes=schemes,
    )
    _print_json(
        {
            "input": args.input,
            "output": args.output,
            "entries_written": len(report.entries),
            "skipped": report.skipped,
            "expanded": report.expanded,
            "deduped": report.deduped,
        }
    )
    return 0


def command_test_proxies(args: argparse.Namespace) -> int:
    schemes = _split_csv(args.expand_schemes)
    config = ProxyTesterConfig(
        input_path=Path(args.input),
        working_output=Path(args.output),
        json_output=Path(args.json_output),
        target_url=args.target,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        missing_scheme_mode=args.missing_scheme_mode,
        assumed_scheme=args.assume_scheme,
        expansion_schemes=schemes,
        detect_origin=args.detect_origin,
        max_retries=args.max_retries,
        retry_backoff_base_s=args.retry_backoff_base,
        retry_backoff_cap_s=args.retry_backoff_cap,
        seed=args.seed,
    )
    payload = asyncio.run(test_proxies(config))
    _print_json(
        {
            "source": payload["source"],
            "target": payload["target"],
            "total_tested": payload["total"],
            "working": payload["working"],
            "latency_summary_ms": payload["latency_summary_ms"],
            "working_by_scheme": payload["working_by_scheme"],
            "working_by_anonymity": payload["working_by_anonymity"],
            "working_output": args.output,
            "json_output": args.json_output,
            "parse": payload["parse"],
        }
    )
    return 0


def command_pool_report(args: argparse.Namespace) -> int:
    pool = ProxyPool.from_results_file(
        Path(args.results),
        PoolConfig(
            cooldown_seconds=args.cooldown,
            ewma_alpha=args.ewma_alpha,
            breaker_failure_threshold=args.breaker_threshold,
            breaker_reset_timeout_s=args.breaker_reset,
        ),
        strategy_name=args.strategy,
    )
    _print_json(pool.stats())
    return 0


def command_run_lab(args: argparse.Namespace) -> int:
    # Validate strategy name early so bad input fails before any I/O.
    _validate_strategy(args.strategy)
    config = LabRunConfig(
        events=args.events,
        input_results=Path(args.results),
        output_jsonl=Path(args.output),
        seed=args.seed,
        adapter_name=args.adapter,
        strategy_name=args.strategy,
        sticky_key=args.sticky_key,
        pool=PoolConfig(
            cooldown_seconds=args.cooldown,
            ewma_alpha=args.ewma_alpha,
            breaker_failure_threshold=args.breaker_threshold,
            breaker_reset_timeout_s=args.breaker_reset,
        ),
    )
    _print_json(asyncio.run(run_lab(config)))
    return 0


def command_probe_infra(args: argparse.Namespace) -> int:
    config = ProbeInfraConfig(
        output_path=Path(args.output),
        concurrency=args.concurrency,
        categories=_split_csv(args.category),
        names=_split_csv(args.name),
        seed=args.seed,
        use_pool=args.use_pool,
        pool_results_path=Path(args.pool_results) if args.pool_results else None,
        pool_strategy=args.pool_strategy,
    )
    runner_cfg = ProbeRunConfig(
        output_path=config.output_path,
        concurrency=config.concurrency,
        categories=config.categories,
        names=config.names,
        seed=config.seed,
        use_pool=config.use_pool,
        pool_results_path=config.pool_results_path,
        pool_strategy=config.pool_strategy,
    )
    payload = asyncio.run(run_probe(runner_cfg))
    # Compact summary on stdout; full results in the JSON file.
    _print_json(
        {
            "output_path": str(config.output_path),
            "total_targets": payload["total_targets"],
            "ok": payload["ok"],
            "failed": payload["failed"],
            "by_category": payload["by_category"],
            "latency_summary_ms": payload["latency_summary_ms"],
            "via_proxy_pool": payload["via_proxy_pool"],
        }
    )
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viewbot-lab",
        description="Proxy diagnostics and self-infrastructure blackbox probing lab",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- inspect -------------------------------------------------------------
    inspect = sub.add_parser("inspect", help="show lineage and safety policy")
    inspect.set_defaults(handler="inspect")

    # -- normalize-proxies ---------------------------------------------------
    normalize = sub.add_parser("normalize-proxies", help="normalize/expand proxy list schemes")
    normalize.add_argument("-i", "--input", required=True)
    normalize.add_argument("-o", "--output", default="normalized-proxies.txt")
    normalize.add_argument("--missing-scheme-mode", choices=["reject", "assume", "expand"], default="expand")
    normalize.add_argument("--assume-scheme", choices=["http", "https", "socks4", "socks5"], default="http")
    normalize.add_argument("--expand-schemes", default="http,https,socks4,socks5")
    normalize.set_defaults(handler="normalize-proxies")

    # -- test-proxies --------------------------------------------------------
    tester = sub.add_parser("test-proxies", help="test proxies against allowed reflector endpoints")
    tester.add_argument("-i", "--input", required=True)
    tester.add_argument("-o", "--output", default="working-proxies.txt")
    tester.add_argument("--json-output", default="proxy-results.json")
    tester.add_argument("--target", default="https://httpbin.org/anything")
    tester.add_argument("-c", "--concurrency", type=int, default=50)
    tester.add_argument("-t", "--timeout", type=float, default=10.0)
    tester.add_argument("--missing-scheme-mode", choices=["reject", "assume", "expand"], default="expand")
    tester.add_argument("--assume-scheme", choices=["http", "https", "socks4", "socks5"], default="http")
    tester.add_argument("--expand-schemes", default="http,https,socks4,socks5")
    tester.add_argument("--detect-origin", action="store_true")
    tester.add_argument("--max-retries", type=int, default=0,
                        help="retries per proxy on failure; total attempts = 1 + max_retries")
    tester.add_argument("--retry-backoff-base", type=float, default=0.25)
    tester.add_argument("--retry-backoff-cap", type=float, default=2.0)
    tester.add_argument("--seed", type=int, default=1337)
    tester.set_defaults(handler="test-proxies")

    # -- pool-report ---------------------------------------------------------
    pool = sub.add_parser("pool-report", help="summarise working-proxy results as a health-aware pool")
    pool.add_argument("--results", default="proxy-results.json")
    pool.add_argument("--cooldown", type=float, default=10.0)
    pool.add_argument("--ewma-alpha", type=float, default=0.2)
    pool.add_argument("--breaker-threshold", type=int, default=5)
    pool.add_argument("--breaker-reset", type=float, default=30.0)
    pool.add_argument("--strategy", default="weighted_random",
                      choices=["weighted_random", "round_robin", "sticky", "lowest_latency"])
    pool.set_defaults(handler="pool-report")

    # -- run-lab -------------------------------------------------------------
    lab = sub.add_parser("run-lab", help="run orchestration simulation OR async adapter sweep")
    lab.add_argument("--results", default="proxy-results.json")
    lab.add_argument("-o", "--output", default="lab-events.jsonl")
    lab.add_argument("--events", type=int, default=20)
    lab.add_argument("--seed", type=int, default=1337)
    lab.add_argument("--cooldown", type=float, default=10.0)
    lab.add_argument("--ewma-alpha", type=float, default=0.2)
    lab.add_argument("--breaker-threshold", type=int, default=5)
    lab.add_argument("--breaker-reset", type=float, default=30.0)
    lab.add_argument("--adapter", default="local_echo", choices=list(KNOWN_ADAPTERS))
    lab.add_argument("--strategy", default="weighted_random",
                     choices=["weighted_random", "round_robin", "sticky", "lowest_latency"])
    lab.add_argument("--sticky-key", default=None,
                     help="when --strategy=sticky, key to bind events to a single proxy")
    lab.set_defaults(handler="run-lab")

    # -- probe-infra ---------------------------------------------------------
    probe = sub.add_parser("probe-infra",
                           help="health probes against your declared infrastructure inventory")
    probe.add_argument("-o", "--output", default="infra-probe-results.json")
    probe.add_argument("-c", "--concurrency", type=int, default=8)
    probe.add_argument("--category", default="",
                       help="comma-separated category filter (litehaus,fastping,mars-api,whispr,80days)")
    probe.add_argument("--name", default="",
                       help="comma-separated explicit target names")
    probe.add_argument("--seed", type=int, default=1337)
    probe.add_argument("--use-pool", action="store_true",
                       help="route probes through proxies from --pool-results")
    probe.add_argument("--pool-results", default=None,
                       help="path to proxy-results.json (required with --use-pool)")
    probe.add_argument("--pool-strategy", default="weighted_random",
                       choices=["weighted_random", "round_robin", "sticky", "lowest_latency"])
    probe.set_defaults(handler="probe-infra")

    return parser


def build_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register("inspect", command_inspect)
    registry.register("normalize-proxies", command_normalize)
    registry.register("test-proxies", command_test_proxies)
    registry.register("pool-report", command_pool_report)
    registry.register("run-lab", command_run_lab)
    registry.register("probe-infra", command_probe_infra)
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = build_registry()
    try:
        handler = registry.get(args.handler)
        return handler(args)
    except SafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
