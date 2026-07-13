"""
monotonic timing helpers so latency measurements are not affected by wall clock skew
uses time monotonic instead of system time to avoid NTP jumps
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

WIB_OFFSET_HOURS = 7
WIB_OFFSET = timedelta(hours=WIB_OFFSET_HOURS)  # WIB is UTC+7 (CT-001)


def ensure_utc(value: datetime | None) -> datetime | None:
    """forces a datetime to have utc timezone if it has none"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Stopwatch:
    """tracks elapsed milliseconds from a monotonic start time"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def elapsed_ms(self) -> int:
        """returns milliseconds elapsed since initialization"""
        return int((self._clock() - self._start) * 1000)

