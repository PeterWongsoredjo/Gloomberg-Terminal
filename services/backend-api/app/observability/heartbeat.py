from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from app.observability import operator_log


class Heartbeat:
    """monitors last recorded beat timestamp using monotonic clock"""

    def __init__(
        self,
        *,
        interval_seconds: float,
        stale_after_seconds: float,
        source: str = "observability",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval_seconds
        self._stale_after = stale_after_seconds
        self._source = source
        self._clock = clock
        self._last_beat = clock()

    def beat(self) -> None:
        """records current timestamp and logs the heartbeat to operator output"""
        self._last_beat = self._clock()
        operator_log.log_heartbeat(self._source)

    def silent_seconds(self) -> float:
        """returns seconds elapsed since the last recorded beat"""
        return self._clock() - self._last_beat

    def is_stale(self) -> bool:
        """returns true if duration since last beat exceeds threshold limit"""
        return self.silent_seconds() > self._stale_after

    def check(self) -> dict[str, str | float] | None:
        """returns critical alert metadata if the loop is stale otherwise none"""
        if not self.is_stale():
            return None
        silent = self.silent_seconds()
        operator_log.log_dead_heartbeat(self._source, silent)
        return {"alert_id": "observability_heartbeat_dead", "severity": "CRITICAL", "silent_seconds": silent}

    async def run(self) -> None:
        """background loop that sends beats on the specified interval"""
        while True:
            self.beat()
            await asyncio.sleep(self._interval)

