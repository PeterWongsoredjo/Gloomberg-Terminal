"""AG-06 circuit breaker: stops burning rate budget on a provider that keeps failing.

Consecutive failures inside the window trip the breaker OPEN and short-circuit that provider.
After the cooldown it goes HALF_OPEN and allows one probe; a success closes it, a failure
re-opens it. The clock is injectable so tests can advance time without waiting.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from app.agentic.providers.limits import BreakerConfig

BreakerState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class CircuitBreaker:
    """A per-provider breaker tracking consecutive failures within a rolling window."""

    def __init__(self, config: BreakerConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._failures = 0
        self._first_failure_at = 0.0
        self._opened_at = 0.0
        self._state: BreakerState = "CLOSED"

    @property
    def failure_count(self) -> int:
        """Consecutive failures counted in the current window."""
        return self._failures

    @property
    def state(self) -> BreakerState:
        """Current state, resolving an elapsed cooldown into a HALF_OPEN probe."""
        if self._state == "OPEN" and self._clock() - self._opened_at >= self._config.cooldown_seconds:
            self._state = "HALF_OPEN"
        return self._state

    def allow(self) -> bool:
        """True when a call may go to this provider right now."""
        return self.state != "OPEN"

    def record_success(self) -> None:
        """A good call clears the failure streak and closes the breaker."""
        self._failures = 0
        self._state = "CLOSED"

    def record_failure(self) -> None:
        """A bad call advances the streak and trips OPEN at the threshold."""
        now = self._clock()
        if self._failures == 0 or now - self._first_failure_at > self._config.window_seconds:
            self._first_failure_at = now
            self._failures = 1
        else:
            self._failures += 1
        if self._state == "HALF_OPEN" or self._failures >= self._config.failure_threshold:
            self._state = "OPEN"
            self._opened_at = now
