"""CT-005 trading-day resolver: is the market open on a given WIB date?

Holidays and working-Saturday exceptions are effective-dated config in the calendar seed,
never hardcoded constants (ADR-006). The weekend is the only structural rule; everything
else is an explicit override row in ref_market_calendar.csv.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import NamedTuple


class _Override(NamedTuple):
    """One effective-dated calendar exception row."""

    is_trading: bool
    reason: str


def _load_overrides(calendar_seed: Path) -> dict[date, _Override]:
    """Reads the explicit is_trading_day / reason exceptions from the calendar seed once."""
    overrides: dict[date, _Override] = {}
    if not calendar_seed.exists():
        return overrides
    with calendar_seed.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row["trade_date"].strip())
            is_trading = row["is_trading_day"].strip().lower() == "true"
            overrides[day] = _Override(is_trading, row.get("holiday_reason", "").strip())
    return overrides


def is_trading_day(day: date, calendar_seed: Path) -> bool:
    """True when the market trades: a weekday, unless an override row says otherwise."""
    override = _load_overrides(calendar_seed).get(day)
    if override is not None:
        return override.is_trading
    return day.weekday() < 5  # Monday-Friday


def holiday_reason(day: date, calendar_seed: Path) -> str:
    """Human-readable reason a non-trading day is closed, for the skip log."""
    if day.weekday() >= 5:
        return "weekend"
    override = _load_overrides(calendar_seed).get(day)
    return (override.reason if override else "") or "non-trading day"
