""" 
circuit breaker: stops burning rate budget on a provider that keeps failing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from app.agentic.providers.limits import BreakerConfig

BreakerState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class CircuitBreaker:
    def __init__(self, config: BreakerConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state: BreakerState = "CLOSED"

    @property
    def failure_count(self) -> int:
        return self._failures

    @property
    def state(self) -> BreakerState:
        if self._state == "OPEN" and self._clock() - self._opened_at >= self._config.cooldown_seconds:
            self._state = "HALF_OPEN"
        return self._state

    def allow(self) -> bool:
        return self.state != "OPEN"

    def record_success(self) -> None:
        self._failures = 0
        self._state = "CLOSED"

    def record_failure(self) -> None:
        # counts consecutive failures, so an hourly flow can still trip this
        self._failures += 1
        if self._state == "HALF_OPEN" or self._failures >= self._config.failure_threshold:
            self._state = "OPEN"
            self._opened_at = self._clock()
