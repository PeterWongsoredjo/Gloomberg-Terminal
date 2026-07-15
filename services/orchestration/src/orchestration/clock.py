"""Shared time helpers so every flow derives the WIB trade date one way."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

WIB_OFFSET_HOURS = 7


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def wib_today() -> date:
    """Today's trade date in Jakarta time."""
    return (now_utc() + timedelta(hours=WIB_OFFSET_HOURS)).date()


def coerce_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else wib_today()
