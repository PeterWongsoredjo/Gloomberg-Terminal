"""
Finds which issuers a headline is actually about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.reference.aliases import normalize
from pipeline.reference.securities import Security, alias_index

_CODE = re.compile(r"\b[A-Z]{4}\b")
_NON_WORD = re.compile(r"[^0-9A-Za-z]")


@dataclass(frozen=True)
class MatchResult:
    """Registry-confirmed tickers, plus the raw code candidates that produced them."""

    tickers: list[str]
    candidates: list[str]


def _clean(*texts: str) -> str:
    """Blanks punctuation without moving anything, so match offsets stay comparable."""
    return _NON_WORD.sub(" ", " ".join(t for t in texts if t))


def _alias_pattern(alias: str) -> str:
    """One alias as a whitespace-tolerant phrase pattern."""
    return r"\b" + r"\s+".join(re.escape(word) for word in alias.split(" ")) + r"\b"


class Registry:
    """The registry in the shape the matcher needs: a code set and an alias regex."""

    def __init__(self, securities: list[Security]) -> None:
        self._tickers = {s.ticker for s in securities}
        self._aliases = alias_index(securities)
        ordered = sorted(self._aliases, key=len, reverse=True)
        self._alias_re = (
            re.compile("|".join(_alias_pattern(a) for a in ordered), re.IGNORECASE) if ordered else None
        )

    def __len__(self) -> int:
        return len(self._tickers)

    def knows(self, ticker: str) -> bool:
        return ticker in self._tickers

    def _code_hits(self, text: str) -> list[tuple[int, str]]:
        return [(m.start(), m.group()) for m in _CODE.finditer(text) if m.group() in self._tickers]

    def _name_hits(self, text: str) -> list[tuple[int, str]]:
        if self._alias_re is None:
            return []
        hits = []
        for match in self._alias_re.finditer(text):
            ticker = self._aliases.get(normalize(match.group()))
            if ticker is not None:
                hits.append((match.start(), ticker))
        return hits

    def match(self, *texts: str) -> MatchResult:
        """Every issuer named in the text, in the order they first appear."""
        haystack = _clean(*texts)
        hits = sorted(self._code_hits(haystack) + self._name_hits(haystack))

        tickers: dict[str, None] = {}
        for _, ticker in hits:
            tickers.setdefault(ticker, None)

        candidates: dict[str, None] = {}
        for match in _CODE.finditer(haystack):
            candidates.setdefault(match.group(), None)

        return MatchResult(tickers=list(tickers), candidates=list(candidates))
