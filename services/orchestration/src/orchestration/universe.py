"""Loads the hand-curated ticker universe, failing closed to empty on any bad edit."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^[A-Z]{4}$")

INDEX_SUBJECTS = frozenset({"IHSG"})


def _ticker_of(entry: Any) -> str | None:
    """One entry's validated ticker, None when the entry is malformed."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("ticker")
    if not isinstance(raw, str):
        return None
    ticker = raw.strip().upper()
    return ticker if _TICKER_RE.fullmatch(ticker) else None


def load_universe(path: Path) -> list[str]:
    """The curated tickers in file order, or empty when anything is wrong."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("universe file unreadable, degrading to empty universe: %s", exc)
        return []
    entries = doc.get("universe") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        logger.warning("universe file %s has no 'universe' list, degrading to empty universe", path)
        return []
    tickers: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        ticker = _ticker_of(entry)
        if ticker is None:
            logger.warning("malformed universe entry %r, degrading to empty universe", entry)
            return []
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers
