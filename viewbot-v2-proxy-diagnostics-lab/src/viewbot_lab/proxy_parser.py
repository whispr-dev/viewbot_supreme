"""
Proxy list parser.

Accepts lines like:
    1.2.3.4:8080
    http://1.2.3.4:8080
    socks5://[2001:db8::1]:1080
    socks5://example.proxy.local:1080

Comments (`#...`) and blank lines are ignored. The parser is *strict*
about hostname/port validity and tolerant about case and whitespace.

IPv6 literals: must appear inside square brackets in the input (RFC 3986)
since the colon is otherwise ambiguous against the port separator. We
strip the brackets before storing so the model layer doesn't carry URL
syntax in its host field; brackets are re-added at URL composition time.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Iterable

from .proxy_models import ProxyEntry, SUPPORTED_SCHEMES


# Scheme + host (bracketed-IPv6 OR plain) + port.
SCHEMED_PROXY_RE = re.compile(
    r"^(?P<scheme>https?|socks[45])://"
    r"(?:\[(?P<v6>[0-9a-fA-F:]+)\]|(?P<host>[A-Za-z0-9_.-]+))"
    r":(?P<port>\d{1,5})$",
    re.IGNORECASE,
)
BARE_PROXY_RE = re.compile(
    r"^(?:\[(?P<v6>[0-9a-fA-F:]+)\]|(?P<host>[A-Za-z0-9_.-]+))"
    r":(?P<port>\d{1,5})$"
)


@dataclass(frozen=True)
class ParseReport:
    entries: tuple[ProxyEntry, ...]
    skipped: int
    expanded: int
    deduped: int


def _valid_host(host: str) -> bool:
    cleaned = host.strip()
    if not cleaned:
        return False
    try:
        ipaddress.ip_address(cleaned)
        return True
    except ValueError:
        # Permissive domain check — internal DNS names like `mars.litehaus`
        # should be acceptable.
        return all(part for part in cleaned.split(".")) and not any(c.isspace() for c in cleaned)


def _valid_port(port: int) -> bool:
    return 1 <= port <= 65535


def _extract_host(match: re.Match[str]) -> str:
    v6 = match.group("v6")
    if v6:
        return v6
    return match.group("host")


def parse_proxy_line(
    raw_line: str,
    *,
    missing_scheme_mode: str = "expand",
    assumed_scheme: str = "http",
    expansion_schemes: Iterable[str] = ("http", "https", "socks4", "socks5"),
) -> tuple[ProxyEntry, ...]:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ()

    schemed = SCHEMED_PROXY_RE.match(line)
    if schemed:
        scheme = schemed.group("scheme").lower()
        host = _extract_host(schemed)
        port = int(schemed.group("port"))
        if scheme not in SUPPORTED_SCHEMES or not _valid_host(host) or not _valid_port(port):
            return ()
        return (ProxyEntry(scheme=scheme, host=host, port=port),)

    bare = BARE_PROXY_RE.match(line)
    if bare:
        host = _extract_host(bare)
        port = int(bare.group("port"))
        if not _valid_host(host) or not _valid_port(port):
            return ()
        if missing_scheme_mode == "reject":
            return ()
        if missing_scheme_mode == "assume":
            return (ProxyEntry(scheme=assumed_scheme, host=host, port=port),)
        if missing_scheme_mode == "expand":
            return tuple(ProxyEntry(scheme=s, host=host, port=port) for s in expansion_schemes)

    return ()


def parse_proxy_text(
    text: str,
    *,
    missing_scheme_mode: str = "expand",
    assumed_scheme: str = "http",
    expansion_schemes: Iterable[str] = ("http", "https", "socks4", "socks5"),
) -> ParseReport:
    entries: list[ProxyEntry] = []
    skipped = 0
    expanded = 0
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        parsed = parse_proxy_line(
            raw,
            missing_scheme_mode=missing_scheme_mode,
            assumed_scheme=assumed_scheme,
            expansion_schemes=expansion_schemes,
        )
        if not parsed:
            skipped += 1
            continue
        if len(parsed) > 1:
            expanded += len(parsed) - 1
        entries.extend(parsed)

    seen: set[tuple[str, str, int]] = set()
    unique: list[ProxyEntry] = []
    deduped = 0
    for entry in entries:
        key = (entry.scheme, entry.host, entry.port)
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        unique.append(entry)
    return ParseReport(entries=tuple(unique), skipped=skipped, expanded=expanded, deduped=deduped)
