"""
Data Validation Gate, pulling the active ticker directory from DuckDB
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agentic.warehouse import GoldReader

_TICKER = re.compile(r"^[A-Z]{4}$")


@dataclass(frozen=True)
class Resolution:
    resolved: list[str]
    unresolved: list[str]


class EntityResolver:
    def __init__(self, universe: set[str]) -> None:
        self._universe = universe

    @classmethod
    async def from_gold(cls, gold: GoldReader) -> EntityResolver:
        return cls(await gold.current_tickers())

    def is_known(self, ticker: str) -> bool:
        return bool(_TICKER.match(ticker)) and ticker in self._universe

    def resolve(self, tickers: list[str]) -> Resolution:
        """Partitions emitted tickers into resolved and unresolved, order-stable."""
        resolved: list[str] = []
        unresolved: list[str] = []
        seen: set[str] = set()
        for ticker in tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
            (resolved if self.is_known(ticker) else unresolved).append(ticker)
        return Resolution(resolved=resolved, unresolved=unresolved)
