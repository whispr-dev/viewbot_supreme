from __future__ import annotations

from urllib.parse import urlparse


class SafetyError(ValueError):
    pass


BLOCKED_TARGET_HOST_HINTS = (
    "medium.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "twitch.tv",
    "spotify.com",
    "soundcloud.com",
)


def assert_reflector_target_allowed(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SafetyError("target must be an absolute http(s) URL")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise SafetyError("target URL has no hostname")
    if any(host == blocked or host.endswith(f".{blocked}") for blocked in BLOCKED_TARGET_HOST_HINTS):
        raise SafetyError(
            f"blocked target {host!r}; this lab only tests proxies against reflector/echo endpoints"
        )
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        allowed = ", ".join(allowed_hosts)
        raise SafetyError(
            f"target host {host!r} is not in the allowed reflector set: {allowed}"
        )
