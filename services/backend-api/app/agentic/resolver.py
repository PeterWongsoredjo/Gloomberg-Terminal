"""
Data Validation Gate: is this ticker a real IDX security?

The registry answers first because it is always populated, even before Gold has
ever been built. Falling back to an empty universe would silently make every
article unscoreable, which is exactly the failure this ordering exists to prevent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import asyncpg

from app.agentic.warehouse import GoldReader

logger = logging.getLogger(__name__)

_TICKER = re.compile(r"^[A-Z]{4}$")

INDEX_SUBJECTS = frozenset({"IHSG"})

_REGISTRY_SQL = "select ticker from reference.idx_security where is_current"


@dataclass(frozen=True)
class Resolution:
    resolved: list[str]
    unresolved: list[str]


class EntityResolver:
    def __init__(self, universe: set[str]) -> None:
        self._universe = universe

    def __len__(self) -> int:
        return len(self._universe)

    @classmethod
    async def from_gold(cls, gold: GoldReader) -> EntityResolver:
        return cls(await gold.current_tickers())

    @classmethod
    async def from_registry(cls, pool: asyncpg.Pool | None, gold: GoldReader) -> EntityResolver:
        """The registry, or Gold when Postgres has nothing, whichever knows more tickers."""
        registry: set[str] = set()
        if pool is not None:
            try:
                rows = await pool.fetch(_REGISTRY_SQL)
                registry = {str(r["ticker"]) for r in rows}
            except (asyncpg.PostgresError, OSError):
                logger.warning("security registry unreadable, falling back to Gold", exc_info=True)
        if registry:
            return cls(registry)
        return await cls.from_gold(gold)

    def is_known(self, ticker: str) -> bool:
        if ticker in INDEX_SUBJECTS:
            return True
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
