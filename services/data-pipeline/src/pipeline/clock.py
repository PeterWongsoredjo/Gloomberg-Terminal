"""Shared time helpers so every service derives the WIB trade date one way."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

WIB_OFFSET_HOURS = 7


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def wib_date(moment: datetime) -> date:
    """The Jakarta calendar date an instant falls on."""
    return (moment.astimezone(timezone.utc) + timedelta(hours=WIB_OFFSET_HOURS)).date()


def wib_today() -> date:
    """Today's trade date in Jakarta time."""
    return wib_date(now_utc())
