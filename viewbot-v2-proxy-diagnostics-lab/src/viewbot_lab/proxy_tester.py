"""
Proxy tester — concurrent diagnostic runs against an allowed reflector.

Upgrades over the previous version:
- SOCKS4/SOCKS5 fully supported (was URL-routed only).
- Per-request retry budget with exponential backoff + jitter.
- TLS handshake info (version/cipher/peer CN) captured when the reflector
  is HTTPS.
- IPv6 hosts handled end-to-end (parser brackets + connector composition).
- Aggregate output includes latency percentiles (p50/p90/p95/p99) and a
  by-scheme working breakdown.

Reflector targets remain gated to the allow-list in `safety.py`. The
default reflector — httpbin.org/anything — exists specifically to be a
header/origin mirror, which is what `determine_anonymity` reads.
"""
from __future__ import annotations

import asyncio
import json
import random
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import ProxyTesterConfig
from .io_utils import atomic_text_writer, read_text, write_json_atomic
from .percentiles import summarize
from .proxy_models import ProxyEntry, ProxyResult, TlsHandshakeInfo
from .proxy_parser import ParseReport, parse_proxy_text
from .safety import assert_reflector_target_allowed

USER_AGENT = "viewbot-v2-safe-proxy-diagnostics/2.1"
ORIGIN_PROBE_URL = "https://api.ipify.org?format=json"
SPEED_FAST_MS = 200
SPEED_MEDIUM_MS = 800

PROXY_IDENTIFYING_HEADERS = frozenset({
    "via",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "x-proxy-id",
    "proxy-connection",
    "x-bluecoat-via",
})


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def determine_speed(latency_ms: float) -> str:
    if latency_ms < SPEED_FAST_MS:
        return "fast"
    if latency_ms < SPEED_MEDIUM_MS:
        return "medium"
    return "slow"


def _first_public_forwarded_ip(headers: dict[str, str]) -> Optional[str]:
    import ipaddress

    xff = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
    if not xff:
        return None
    ips = [ip.strip() for ip in xff.split(",")]
    for ip_str in ips:
        try:
            if not ipaddress.ip_address(ip_str).is_private:
                return ip_str
        except ValueError:
            continue
    return ips[0] if ips else None


def determine_anonymity(
    reflected_headers: dict[str, str],
    reflected_origin: Optional[str],
    our_origin_ip: Optional[str],
) -> str:
    """
    transparent → reflector saw our real public IP (or one of our headers leaked it)
    anonymous   → reflector saw proxy-revealing headers but not our real IP
    elite       → reflector saw neither
    """
    headers_lower = {k.lower(): str(v) for k, v in reflected_headers.items()}
    if our_origin_ip:
        leaked_in_origin = reflected_origin is not None and our_origin_ip in reflected_origin
        leaked_in_headers = any(our_origin_ip in v for v in headers_lower.values())
        if leaked_in_origin or leaked_in_headers:
            return "transparent"
    if any(header in headers_lower for header in PROXY_IDENTIFYING_HEADERS):
        return "anonymous"
    return "elite"


# ---------------------------------------------------------------------------
# Networking primitives
# ---------------------------------------------------------------------------


def _lazy_aiohttp() -> tuple[Any, Any, Any]:
    try:
        import aiohttp
        from aiohttp import ClientTimeout
        from aiohttp_socks import ProxyConnector
    except ImportError as exc:
        raise RuntimeError(
            "missing dependency. Install with: python -m pip install -r requirements.txt"
        ) from exc
    return aiohttp, ClientTimeout, ProxyConnector


def _extract_reflection(payload: Any, response_headers: dict[str, str]) -> tuple[Optional[str], dict[str, str]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("headers"), dict):
            headers = {str(k): str(v) for k, v in payload["headers"].items()}
        elif isinstance(payload.get("headers_received"), dict):
            headers = {str(k): str(v) for k, v in payload["headers_received"].items()}
        else:
            headers = dict(response_headers)
        origin = payload.get("origin") or payload.get("connecting_ip") or payload.get("ip")
        if origin is not None:
            origin = str(origin)
        return origin, headers
    return None, dict(response_headers)


def _tls_from_response(response) -> Optional[TlsHandshakeInfo]:
    """Best-effort TLS info pull from the live aiohttp response. Never throws."""
    try:
        conn = response.connection
        if conn is None:
            return None
        transport = getattr(conn, "transport", None)
        if transport is None:
            return None
        ssl_obj = transport.get_extra_info("ssl_object")
        if ssl_obj is None:
            return None
        cipher_tuple = ssl_obj.cipher()
        peer_cn: Optional[str] = None
        cert = transport.get_extra_info("peercert")
        if cert and isinstance(cert, dict):
            for rdn in cert.get("subject") or ():
                for kv in rdn:
                    if kv and kv[0] == "commonName":
                        peer_cn = str(kv[1])
                        break
                if peer_cn:
                    break
        return TlsHandshakeInfo(
            version=ssl_obj.version(),
            cipher=cipher_tuple[0] if cipher_tuple else None,
            peer_subject_cn=peer_cn,
        )
    except Exception:
        return None


async def detect_origin_ip(timeout_seconds: float = 10.0) -> Optional[str]:
    aiohttp, ClientTimeout, _ = _lazy_aiohttp()
    try:
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=timeout_seconds)) as session:
            async with session.get(ORIGIN_PROBE_URL, headers={"User-Agent": USER_AGENT}) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
                value = payload.get("ip")
                return str(value) if value else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Single-proxy attempt + retry wrapper
# ---------------------------------------------------------------------------


async def _attempt_once(
    entry: ProxyEntry,
    *,
    target_url: str,
    timeout_seconds: float,
    our_origin_ip: Optional[str],
) -> ProxyResult:
    aiohttp, ClientTimeout, ProxyConnector = _lazy_aiohttp()
    started = time.perf_counter()
    try:
        connector = ProxyConnector.from_url(entry.connector_url, rdns=True)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=timeout_seconds),
            headers=headers,
        ) as session:
            async with session.get(target_url) as response:
                text = await response.text()
                elapsed = (time.perf_counter() - started) * 1000.0
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None
                reflected_origin, reflected_headers = _extract_reflection(payload, dict(response.headers))
                client_ip = _first_public_forwarded_ip(reflected_headers)
                tls = _tls_from_response(response) if target_url.startswith("https://") else None
                return ProxyResult(
                    proxy=entry.url,
                    scheme=entry.scheme,
                    ok=200 <= response.status < 400,
                    latency_ms=round(elapsed, 3),
                    connecting_ip=reflected_origin,
                    client_ip_from_headers=client_ip,
                    anonymity_level=determine_anonymity(reflected_headers, reflected_origin, our_origin_ip),
                    speed_hint=determine_speed(elapsed),
                    headers_received=reflected_headers,
                    http_status=response.status,
                    error=None if 200 <= response.status < 400 else f"HTTP {response.status}",
                    tls=tls,
                    is_ipv6=entry.is_ipv6,
                )
    except Exception as exc:
        return ProxyResult(
            proxy=entry.url,
            scheme=entry.scheme,
            ok=False,
            error=f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__,
            is_ipv6=entry.is_ipv6,
        )


async def test_one_proxy(
    entry: ProxyEntry,
    *,
    target_url: str,
    timeout_seconds: float,
    our_origin_ip: Optional[str],
    max_retries: int = 0,
    retry_backoff_base_s: float = 0.25,
    retry_backoff_cap_s: float = 2.0,
    rng: Optional[random.Random] = None,
) -> ProxyResult:
    """Run an attempt with retry budget; returns the final ProxyResult."""
    rng = rng or random.Random()
    last: Optional[ProxyResult] = None
    for attempt_number in range(1, max_retries + 2):
        result = await _attempt_once(
            entry,
            target_url=target_url,
            timeout_seconds=timeout_seconds,
            our_origin_ip=our_origin_ip,
        )
        result.attempts = attempt_number
        last = result
        if result.ok:
            return result
        if attempt_number > max_retries:
            break
        sleep_s = min(retry_backoff_cap_s, retry_backoff_base_s * (2 ** (attempt_number - 1)))
        await asyncio.sleep(rng.uniform(0.0, sleep_s))
    return last  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------


async def test_proxies(config: ProxyTesterConfig) -> dict[str, Any]:
    config.validate()
    assert_reflector_target_allowed(config.target_url, config.allowed_reflector_hosts)
    text = read_text(config.input_path)
    parse_report = parse_proxy_text(
        text,
        missing_scheme_mode=config.missing_scheme_mode,
        assumed_scheme=config.assumed_scheme,
        expansion_schemes=config.expansion_schemes,
    )
    entries = list(parse_report.entries)
    origin_ip = await detect_origin_ip(config.timeout_seconds) if config.detect_origin else None
    semaphore = asyncio.Semaphore(config.concurrency)
    rng = random.Random(config.seed)

    async def bounded(entry: ProxyEntry) -> ProxyResult:
        async with semaphore:
            return await test_one_proxy(
                entry,
                target_url=config.target_url,
                timeout_seconds=config.timeout_seconds,
                our_origin_ip=origin_ip,
                max_retries=config.max_retries,
                retry_backoff_base_s=config.retry_backoff_base_s,
                retry_backoff_cap_s=config.retry_backoff_cap_s,
                rng=rng,
            )

    results = await asyncio.gather(*(bounded(entry) for entry in entries))
    working = sorted(
        (r for r in results if r.ok and r.latency_ms is not None),
        key=lambda r: r.latency_ms or 999_999.0,
    )

    # Aggregate stats — percentiles over the working set, plus per-scheme counts.
    working_latencies = [r.latency_ms for r in working if r.latency_ms is not None]
    by_scheme_working: dict[str, int] = {}
    for r in working:
        by_scheme_working[r.scheme] = by_scheme_working.get(r.scheme, 0) + 1
    by_anonymity_working: dict[str, int] = {}
    for r in working:
        if r.anonymity_level:
            by_anonymity_working[r.anonymity_level] = by_anonymity_working.get(r.anonymity_level, 0) + 1

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(config.input_path),
        "target": config.target_url,
        "our_origin_ip_detected": bool(origin_ip),
        "parse": {
            "input_entries": len(entries),
            "skipped": parse_report.skipped,
            "expanded_missing_scheme_entries": parse_report.expanded,
            "deduped": parse_report.deduped,
        },
        "total": len(results),
        "working": len(working),
        "latency_summary_ms": summarize(working_latencies).to_dict(),
        "working_by_scheme": by_scheme_working,
        "working_by_anonymity": by_anonymity_working,
        "results": [result.to_dict() for result in results],
    }

    with atomic_text_writer(config.working_output) as handle:
        for result in working:
            handle.write(result.proxy + "\n")
    write_json_atomic(config.json_output, payload)
    return payload


# ---------------------------------------------------------------------------
# Standalone normalisation utility (also used by the CLI)
# ---------------------------------------------------------------------------


def normalize_proxy_file(
    input_path: Path,
    output_path: Path,
    *,
    missing_scheme_mode: str = "expand",
    assumed_scheme: str = "http",
    expansion_schemes: tuple[str, ...] = ("http", "https", "socks4", "socks5"),
) -> ParseReport:
    report = parse_proxy_text(
        read_text(input_path),
        missing_scheme_mode=missing_scheme_mode,
        assumed_scheme=assumed_scheme,
        expansion_schemes=expansion_schemes,
    )
    with atomic_text_writer(output_path) as handle:
        for entry in report.entries:
            handle.write(entry.url + "\n")
    return report
