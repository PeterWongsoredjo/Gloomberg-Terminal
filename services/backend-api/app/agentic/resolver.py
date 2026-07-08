"""CT-004 entity resolution: a ticker a model emits is verified, never trusted.

Every ticker in an artifact must exist as a current security, or it is dropped to
unresolved_entities. This is the hard gate that keeps a hallucinated or off-market code from
reaching the warehouse as a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agentic.warehouse import GoldReader

_TICKER = re.compile(r"^[A-Z]{4}$")


@dataclass(frozen=True)
class Resolution:
    """The split of emitted tickers into ones that exist and ones that do not."""

    resolved: list[str]
    unresolved: list[str]


class EntityResolver:
    """Validates emitted tickers against the current dim_security universe."""

    def __init__(self, universe: set[str]) -> None:
        self._universe = universe

    @classmethod
    async def from_gold(cls, gold: GoldReader) -> EntityResolver:
        """Builds a resolver from the current securities in Gold."""
        return cls(await gold.current_tickers())

    def is_known(self, ticker: str) -> bool:
        """True when the ticker is a well-formed, currently listed IDX code."""
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
