"""The outbound coalescing buffer that keeps a slow consumer from growing memory.

Pending changes are keyed by security_id, so a burst of updates to the same security collapses to
its latest state. The buffer can never exceed the universe size, so there is no unbounded queue.
"""

from __future__ import annotations

from typing import Any


class CoalescingBuffer:
    """Latest-wins pending tape rows, keyed by security_id."""

    def __init__(self) -> None:
        self._pending: dict[int, dict[str, Any]] = {}

    def push(self, rows: list[dict[str, Any]]) -> None:
        """Adds changed rows, overwriting any earlier pending state for the same security."""
        for row in rows:
            self._pending[int(row["security_id"])] = row

    def peek(self) -> list[dict[str, Any]]:
        """The pending rows without clearing them, so a failed send can be retried."""
        return list(self._pending.values())

    def clear(self) -> None:
        self._pending.clear()

    def __len__(self) -> int:
        return len(self._pending)
