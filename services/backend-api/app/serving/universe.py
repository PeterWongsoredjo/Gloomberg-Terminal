"""Reads the hand-curated ticker universe the insight flows already run on."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings

logger = logging.getLogger("gloomberg.serving.universe")

_TICKER = re.compile(r"^[A-Z]{4}$")


@dataclass(frozen=True)
class UniverseFile:
    """The curated tickers plus when the file was last edited."""

    tickers: tuple[str, ...]
    as_of: datetime


def _ticker_of(entry: Any) -> str | None:
    """One entry's validated ticker, None when the entry is malformed."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("ticker")
    if not isinstance(raw, str):
        return None
    ticker = raw.strip().upper()
    return ticker if _TICKER.fullmatch(ticker) else None


def _tickers_in(doc: Any, path: Path) -> tuple[str, ...]:
    """Every curated ticker in file order, empty when any entry is malformed."""
    entries = doc.get("universe") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        logger.warning("universe file %s has no universe list, serving an empty universe", path)
        return ()
    ordered: dict[str, None] = {}
    for entry in entries:
        ticker = _ticker_of(entry)
        if ticker is None:
            logger.warning("malformed universe entry %r, serving an empty universe", entry)
            return ()
        ordered.setdefault(ticker, None)
    return tuple(ordered)


def _mtime(path: Path) -> float:
    """When the file last changed, zero when it is not there."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def read_universe(path: Path) -> UniverseFile:
    """The curated universe on disk, empty on anything unreadable or malformed."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("universe file unreadable, serving an empty universe: %s", exc)
        return UniverseFile(tickers=(), as_of=datetime.now(UTC))
    return UniverseFile(tickers=_tickers_in(doc, path), as_of=datetime.fromtimestamp(_mtime(path), UTC))


@lru_cache(maxsize=1)
def _cached_universe(path: str, mtime: float) -> UniverseFile:
    return read_universe(Path(path))


def get_universe() -> UniverseFile:
    """The curated universe, re-read only after the file is edited."""
    path = Path(settings.universe_file)
    return _cached_universe(str(path), _mtime(path))
