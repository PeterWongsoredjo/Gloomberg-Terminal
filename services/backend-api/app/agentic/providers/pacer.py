"""
Client-side token buckets that pace requests under the seeded RPM and TPM ceilings.

Batched requests are large enough that requests-per-minute stops being the binding
limit, so tokens-per-minute is paced too.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class _Bucket:
    """One leaky bucket refilling at a fixed per-minute allowance."""

    def __init__(self, per_minute: float, clock: Callable[[], float]) -> None:
        self._capacity = float(per_minute)
        self._refill_per_sec = per_minute / 60.0
        self._tokens = float(per_minute)
        self._clock = clock
        self._updated = clock()

    def replenish(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill_per_sec)
        self._updated = now

    def wait_for(self, amount: float) -> float:
        """Seconds until the bucket could cover the amount, zero when it already can."""
        if self._tokens >= amount:
            return 0.0
        return (amount - self._tokens) / self._refill_per_sec

    def take(self, amount: float) -> None:
        self._tokens -= amount

    def clamp(self, amount: float) -> float:
        """A single request larger than the whole bucket can never be paced, only capped."""
        return min(amount, self._capacity)


class RatePacer:
    def __init__(
        self,
        rpm: int,
        tpm: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._requests = _Bucket(rpm, clock)
        self._tokens = _Bucket(tpm, clock) if tpm else None
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int = 0) -> None:
        """Blocks until both budgets can cover the request, then charges them together."""
        async with self._lock:
            while True:
                self._requests.replenish()
                wait = self._requests.wait_for(1.0)
                cost = 0.0
                if self._tokens is not None and estimated_tokens > 0:
                    self._tokens.replenish()
                    cost = self._tokens.clamp(float(estimated_tokens))
                    wait = max(wait, self._tokens.wait_for(cost))
                if wait <= 0.0:
                    self._requests.take(1.0)
                    if self._tokens is not None and cost:
                        self._tokens.take(cost)
                    return
                await self._sleep(wait)
