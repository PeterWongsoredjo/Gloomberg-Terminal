"""
Client-side token bucket that paces requests under the seeded RPM ceiling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class RatePacer:
    def __init__(
        self,
        rpm: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._capacity = float(rpm)
        self._refill_per_sec = rpm / 60.0
        self._tokens = float(rpm)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _replenish(self) -> None:
        """Adds the tokens that have accrued since the last check."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill_per_sec)
        self._updated = now

    async def acquire(self) -> None:
        """Blocks until one request slot is available, then consumes it."""
        async with self._lock:
            while True:
                self._replenish()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                await self._sleep(deficit / self._refill_per_sec)
