"""Trade-date helpers, re-exported from the pipeline package so there is one WIB rule."""

from __future__ import annotations

from datetime import date

from pipeline.clock import WIB_OFFSET_HOURS, now_utc, wib_date, wib_today

__all__ = ["WIB_OFFSET_HOURS", "coerce_date", "now_utc", "wib_date", "wib_today"]


def coerce_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else wib_today()
