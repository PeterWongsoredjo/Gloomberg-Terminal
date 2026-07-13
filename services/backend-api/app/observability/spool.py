"""
bounded memory buffer that stores telemetry events and flushes them later
evicts oldest low priority events when the buffer overflows to prevent blocking
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from app.observability.catalog import accept
from app.observability.events import SEVERITY_RANK, TelemetryEvent

# only these may be shed when the buffer is full; ERROR/CRITICAL are never dropped
_SHEDDABLE_MAX_RANK = SEVERITY_RANK["WARN"]


class TelemetrySpool:

    def __init__(self, max_events: int) -> None:
        self._max_events = max_events
        self._events: deque[TelemetryEvent] = deque()
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """returns count of events dropped due to buffer limit"""
        return self._dropped

    def __len__(self) -> int:
        return len(self._events)

    def emit(self, event: TelemetryEvent) -> bool:
        """adds event to buffer and handles overflow eviction if needed"""
        if not accept(event.measure.name):
            return False
        if len(self._events) >= self._max_events:
            self._shed_one()
        self._events.append(event)
        return True

    def _shed_one(self) -> None:
        """evicts the first low severity item found in the queue"""
        for index, event in enumerate(self._events):
            if SEVERITY_RANK[event.severity] <= _SHEDDABLE_MAX_RANK:
                del self._events[index]
                self._dropped += 1
                return
        # nothing sheddable: the buffer is all high-severity, so we tolerate the overflow

    def drain(self, sink: Callable[[list[TelemetryEvent]], bool]) -> int:
        """sends buffered events to external sink and clears queue on success"""
        if not self._events:
            return 0
        batch = list(self._events)
        if not sink(batch):
            return 0
        self._events.clear()
        return len(batch)

