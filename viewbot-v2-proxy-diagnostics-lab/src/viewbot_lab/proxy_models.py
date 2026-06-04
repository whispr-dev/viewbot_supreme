"""
Proxy data model.

ProxyEntry — what we have *before* testing (scheme/host/port).
ProxyResult — what we got back *after* testing one. Now includes TLS
              handshake metadata, retry-attempt count, and IPv6-aware
              host display.

IPv6 handling: hostnames are stored bare (no brackets). The `url` property
re-adds brackets when the host is a colon-bearing IPv6 literal so that the
resulting URL parses correctly downstream (aiohttp, urllib, etc.).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

SUPPORTED_SCHEMES = {"http", "https", "socks4", "socks5"}


def _host_for_url(host: str) -> str:
    """Wrap IPv6 literals in brackets for URL composition; leave others alone."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


@dataclass(frozen=True, slots=True)
class ProxyEntry:
    scheme: str
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"{self.scheme}://{_host_for_url(self.host)}:{self.port}"

    @property
    def connector_url(self) -> str:
        # In most public proxy lists, an `https://host:port` entry actually
        # describes an HTTP proxy that supports CONNECT for HTTPS targets,
        # not TLS-to-the-proxy. Normalise that here.
        scheme = "http" if self.scheme in {"http", "https"} else self.scheme
        return f"{scheme}://{_host_for_url(self.host)}:{self.port}"

    @property
    def is_ipv6(self) -> bool:
        return ":" in self.host


@dataclass(slots=True)
class TlsHandshakeInfo:
    """Subset of TLS info we capture from a successful HTTPS request."""

    version: Optional[str] = None
    cipher: Optional[str] = None
    peer_subject_cn: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "cipher": self.cipher,
            "peer_subject_cn": self.peer_subject_cn,
        }


@dataclass(slots=True)
class ProxyResult:
    proxy: str
    scheme: str
    ok: bool
    latency_ms: Optional[float] = None
    connecting_ip: Optional[str] = None
    client_ip_from_headers: Optional[str] = None
    anonymity_level: Optional[str] = None
    speed_hint: Optional[str] = None
    headers_received: dict[str, str] = field(default_factory=dict)
    http_status: Optional[int] = None
    error: Optional[str] = None
    attempts: int = 1
    tls: Optional[TlsHandshakeInfo] = None
    is_ipv6: bool = False

    def to_dict(self) -> dict[str, object]:
        # asdict would walk the TlsHandshakeInfo too, but we want explicit
        # control over the on-disk shape so we serialise it ourselves.
        return {
            "proxy": self.proxy,
            "scheme": self.scheme,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "connecting_ip": self.connecting_ip,
            "client_ip_from_headers": self.client_ip_from_headers,
            "anonymity_level": self.anonymity_level,
            "speed_hint": self.speed_hint,
            "headers_received": self.headers_received,
            "http_status": self.http_status,
            "error": self.error,
            "attempts": self.attempts,
            "tls": self.tls.to_dict() if self.tls else None,
            "is_ipv6": self.is_ipv6,
        }
