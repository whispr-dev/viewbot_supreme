"""
Generic HTTP health probe.

This is the workhorse used by:
- `adapters.py`        for blackbox-exporter-style checks against your own
                       infrastructure (Litehaus beacons, FastPing.it /probe,
                       mail.whispr.dev, mars-api),
- `probe_runner.py`    for the `probe-infra` CLI command,
- `proxy_tester.py`    for per-proxy diagnostic requests against allowed
                       reflectors.

Capabilities
============
- HTTP/HTTPS GET/HEAD with configurable timeout, headers, and method.
- Optional proxy routing via aiohttp_socks (any of http/https/socks4/socks5).
- Retry budget with exponential backoff and jitter — every retry is also
  capped by the wall-clock deadline so a slow target can't burn through
  the budget by hitting `timeout_seconds` on every attempt.
- TLS sanity capture: when the response was retrieved over HTTPS we record
  the negotiated protocol version, cipher, and peer-cert subject CN.
- Response assertions: status range, max latency, expected body substring,
  expected JSON-pointer key, expected header presence.
- IPv6 ready — aiohttp resolves both A and AAAA records; bracketed proxy
  hosts (e.g. socks5://[::1]:1080) parse correctly via the parser.

All public functions are async and return a `ProbeOutcome` rather than
raising — failure is a value, not an exception.
"""
from __future__ import annotations

import asyncio
import json
import random
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Outcome model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TlsInfo:
    version: Optional[str] = None         # e.g. "TLSv1.3"
    cipher: Optional[str] = None          # e.g. "TLS_AES_256_GCM_SHA384"
    peer_subject_cn: Optional[str] = None # e.g. "*.whispr.dev"

    def to_dict(self) -> dict[str, object]:
        return {"version": self.version, "cipher": self.cipher, "peer_subject_cn": self.peer_subject_cn}


@dataclass(slots=True)
class ProbeOutcome:
    target: str
    ok: bool
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    attempts: int = 0
    error: Optional[str] = None
    tls: Optional[TlsInfo] = None
    body_preview: Optional[str] = None
    response_headers: dict[str, str] = field(default_factory=dict)
    assertion_failures: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "ok": self.ok,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "error": self.error,
            "tls": self.tls.to_dict() if self.tls else None,
            "body_preview": self.body_preview,
            "response_headers": self.response_headers,
            "assertion_failures": self.assertion_failures,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Request configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpProbeRequest:
    target: str
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 10.0

    # Retry budget. Total wall-clock spend is hard-capped to
    # (timeout_seconds + retry_backoff_cap * max_retries).
    max_retries: int = 0
    retry_backoff_base_s: float = 0.25
    retry_backoff_cap_s: float = 2.0

    # Routing
    proxy_url: Optional[str] = None  # if set, request is routed through this proxy
    verify_ssl: bool = True

    # Assertions
    expected_status_min: int = 200
    expected_status_max: int = 399
    max_latency_ms: Optional[float] = None
    body_must_contain: Optional[str] = None
    json_must_have_key: Optional[str] = None   # dotted path: e.g. "data.uptime_seconds"
    header_must_be_present: Optional[str] = None
    body_preview_bytes: int = 512


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _lazy_aiohttp():
    """Lazy import so the module can be imported even without aiohttp installed."""
    try:
        import aiohttp
        from aiohttp import ClientTimeout
    except ImportError as exc:
        raise RuntimeError(
            "aiohttp is required; install with: python -m pip install -r requirements.txt"
        ) from exc
    return aiohttp, ClientTimeout


def _lazy_socks_connector():
    try:
        from aiohttp_socks import ProxyConnector
        return ProxyConnector
    except ImportError as exc:
        raise RuntimeError("aiohttp_socks is required for proxy routing") from exc


def _extract_tls_info(response) -> Optional[TlsInfo]:
    """
    Pull TLS handshake details out of the underlying aiohttp connection.

    aiohttp doesn't expose this through a stable public API, so we tread
    carefully and return None on any structural surprise rather than
    raising. Best-effort, never throws.
    """
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
        version = ssl_obj.version()
        cipher_tuple = ssl_obj.cipher()
        cipher = cipher_tuple[0] if cipher_tuple else None
        peer_cn: Optional[str] = None
        cert = transport.get_extra_info("peercert")
        if cert and isinstance(cert, dict):
            subject = cert.get("subject") or ()
            for rdn in subject:
                for kv in rdn:
                    if kv and kv[0] == "commonName":
                        peer_cn = str(kv[1])
                        break
                if peer_cn:
                    break
        return TlsInfo(version=version, cipher=cipher, peer_subject_cn=peer_cn)
    except Exception:
        return None


def _evaluate_assertions(
    request: HttpProbeRequest,
    status: int,
    latency_ms: float,
    headers: dict[str, str],
    body_preview: str,
) -> list[str]:
    failures: list[str] = []
    if not (request.expected_status_min <= status <= request.expected_status_max):
        failures.append(
            f"status {status} outside [{request.expected_status_min}, {request.expected_status_max}]"
        )
    if request.max_latency_ms is not None and latency_ms > request.max_latency_ms:
        failures.append(f"latency {latency_ms:.1f}ms > max {request.max_latency_ms:.1f}ms")
    if request.body_must_contain and request.body_must_contain not in body_preview:
        failures.append(f"body did not contain {request.body_must_contain!r}")
    if request.header_must_be_present:
        wanted = request.header_must_be_present.lower()
        if not any(k.lower() == wanted for k in headers):
            failures.append(f"required header {request.header_must_be_present!r} missing")
    if request.json_must_have_key:
        try:
            payload = json.loads(body_preview)
        except json.JSONDecodeError:
            failures.append(f"response body is not JSON; cannot check key {request.json_must_have_key!r}")
        else:
            cursor: Any = payload
            for part in request.json_must_have_key.split("."):
                if isinstance(cursor, dict) and part in cursor:
                    cursor = cursor[part]
                else:
                    failures.append(f"json key path {request.json_must_have_key!r} not present")
                    break
    return failures


async def _one_attempt(request: HttpProbeRequest) -> ProbeOutcome:
    aiohttp, ClientTimeout = _lazy_aiohttp()

    connector = None
    ssl_context: ssl.SSLContext | bool
    if not request.verify_ssl:
        ssl_context = False
    else:
        ssl_context = True  # aiohttp default

    if request.proxy_url:
        ProxyConnector = _lazy_socks_connector()
        # rdns=True ensures hostname resolution happens at the proxy, which
        # is what we want for any non-trivial routing.
        connector = ProxyConnector.from_url(request.proxy_url, rdns=True, ssl=ssl_context if ssl_context is not True else None)

    headers = dict(request.headers) or {}
    headers.setdefault("User-Agent", "viewbot-lab-health-probe/2.1")
    headers.setdefault("Accept", "application/json,text/plain,*/*")

    started = time.perf_counter()
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=request.timeout_seconds),
            headers=headers,
        ) as session:
            kwargs: dict[str, object] = {}
            if connector is None and ssl_context is False:
                # When no proxy, we control SSL via the request-level `ssl` kwarg.
                kwargs["ssl"] = False
            async with session.request(request.method, request.target, **kwargs) as response:
                # Read at most body_preview_bytes; full body isn't needed for a
                # health probe and bounding the read protects against accidental
                # large responses.
                raw = await response.content.read(max(0, int(request.body_preview_bytes)))
                latency_ms = (time.perf_counter() - started) * 1000.0
                try:
                    body_preview = raw.decode("utf-8", errors="replace")
                except Exception:
                    body_preview = ""
                response_headers = {k: v for k, v in response.headers.items()}
                tls = _extract_tls_info(response) if request.target.startswith("https://") else None
                assertion_failures = _evaluate_assertions(
                    request, response.status, latency_ms, response_headers, body_preview
                )
                return ProbeOutcome(
                    target=request.target,
                    ok=not assertion_failures,
                    http_status=response.status,
                    latency_ms=round(latency_ms, 3),
                    attempts=1,
                    error=None if not assertion_failures else "; ".join(assertion_failures),
                    tls=tls,
                    body_preview=body_preview if body_preview else None,
                    response_headers=response_headers,
                    assertion_failures=assertion_failures,
                )
    except asyncio.TimeoutError:
        return ProbeOutcome(target=request.target, ok=False, attempts=1, error="timeout")
    except Exception as exc:
        return ProbeOutcome(
            target=request.target,
            ok=False,
            attempts=1,
            error=f"{exc.__class__.__name__}: {exc}",
        )


async def probe(request: HttpProbeRequest, rng: random.Random | None = None) -> ProbeOutcome:
    """
    Run a probe with retry budget. Returns a single ProbeOutcome reflecting
    the *final* attempt, with `attempts` counting how many tries were made.
    """
    rng = rng or random.Random()
    last_outcome: ProbeOutcome | None = None
    deadline_total_s = request.timeout_seconds * (1 + request.max_retries) + request.retry_backoff_cap_s * request.max_retries
    overall_start = time.perf_counter()

    for attempt_number in range(1, request.max_retries + 2):
        if time.perf_counter() - overall_start > deadline_total_s:
            break
        outcome = await _one_attempt(request)
        outcome.attempts = attempt_number
        last_outcome = outcome
        if outcome.ok:
            return outcome
        if attempt_number > request.max_retries:
            break
        # Exponential backoff with full jitter; cap at retry_backoff_cap_s.
        sleep_s = min(
            request.retry_backoff_cap_s,
            request.retry_backoff_base_s * (2 ** (attempt_number - 1)),
        )
        sleep_s = rng.uniform(0.0, sleep_s)
        await asyncio.sleep(sleep_s)

    if last_outcome is None:
        # The deadline guard tripped before we ran a single attempt.
        return ProbeOutcome(
            target=request.target,
            ok=False,
            attempts=0,
            error="deadline exceeded before first attempt",
        )
    return last_outcome
