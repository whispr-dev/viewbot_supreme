"""
Per-proxy circuit breaker.

Standard three-state machine (Nygard, "Release It!"):

    CLOSED  ──failures ≥ threshold──▶  OPEN
       ▲                                 │
       │                                 │ reset_timeout elapsed
       │                                 ▼
       │                              HALF_OPEN
       │                                 │
       └────success on probe──────       │
                                         │
                              failure on probe
                                         ▼
                                       OPEN (reset cooldown)

- CLOSED: traffic flows normally. Failures are counted.
- OPEN: requests are short-circuited (the pool skips this proxy entirely).
        After `reset_timeout_s` has elapsed since the breaker opened,
        the next selection attempt transitions to HALF_OPEN.
- HALF_OPEN: a limited number of probe requests are permitted. A single
        success closes the breaker (and resets the failure count). A single
        failure re-opens it and restarts the cooldown clock.

We use monotonic-clock-style floats (seconds) supplied by the caller so
this module is trivially testable without patching `time.time`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5          # consecutive failures to open
    reset_timeout_s: float = 30.0       # wait before transitioning OPEN → HALF_OPEN
    half_open_max_probes: int = 1       # in-flight probes permitted in HALF_OPEN

    def validate(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.reset_timeout_s <= 0:
            raise ValueError("reset_timeout_s must be > 0")
        if self.half_open_max_probes < 1:
            raise ValueError("half_open_max_probes must be >= 1")


@dataclass(slots=True)
class CircuitBreaker:
    """Mutable circuit breaker state for a single resource (one proxy)."""

    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_in_flight: int = 0

    def allow(self, now: float) -> bool:
        """
        Decide whether a call should be permitted at time `now`.

        Side effect: transitions OPEN → HALF_OPEN if the cooldown has elapsed,
        and reserves an in-flight slot when allowing a HALF_OPEN probe so
        the breaker doesn't dispatch more probes than configured.
        """
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if now - self.opened_at >= self.config.reset_timeout_s:
                self.state = BreakerState.HALF_OPEN
                self.half_open_in_flight = 0
                # fall through into HALF_OPEN handling
            else:
                return False
        if self.state is BreakerState.HALF_OPEN:
            if self.half_open_in_flight < self.config.half_open_max_probes:
                self.half_open_in_flight += 1
                return True
            return False
        return False  # unreachable, but defensive

    def record_success(self) -> None:
        self.consecutive_failures = 0
        if self.state is BreakerState.HALF_OPEN:
            self.half_open_in_flight = max(0, self.half_open_in_flight - 1)
            self.state = BreakerState.CLOSED
        # CLOSED + success: already in steady state, nothing to do.

    def record_failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if self.state is BreakerState.HALF_OPEN:
            # A failure during half-open is loud: re-open immediately and
            # restart the cooldown, regardless of threshold.
            self.half_open_in_flight = max(0, self.half_open_in_flight - 1)
            self.state = BreakerState.OPEN
            self.opened_at = now
            return
        if self.state is BreakerState.CLOSED:
            if self.consecutive_failures >= self.config.failure_threshold:
                self.state = BreakerState.OPEN
                self.opened_at = now

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at,
            "half_open_in_flight": self.half_open_in_flight,
        }
