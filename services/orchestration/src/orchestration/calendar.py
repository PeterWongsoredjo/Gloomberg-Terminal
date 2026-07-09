"""
Holidays and working-Saturday are exceptions in the orchestration
this file dictates that our prefect only runs on days where ihsg is open
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import NamedTuple


class _Override(NamedTuple):
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
    override = _load_overrides(calendar_seed).get(day)
    if override is not None:
        return override.is_trading
    return day.weekday() < 5  # Monday-Friday


def holiday_reason(day: date, calendar_seed: Path) -> str:
    if day.weekday() >= 5:
        return "weekend"
    override = _load_overrides(calendar_seed).get(day)
    return (override.reason if override else "") or "non-trading day"
