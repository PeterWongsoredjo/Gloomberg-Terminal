"""
trading calendar helper containing session phases, close times, and holiday overrides
provides the schedule reference used to determine market open status and stale data limits
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from app.core.enums import SessionPhase
from app.observability.clock import WIB_OFFSET_HOURS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CALENDAR_SEED = _REPO_ROOT / "dbt" / "seeds" / "ref_market_calendar.csv"
_SESSION_WINDOWS = Path(__file__).resolve().parent / "config" / "session_windows.yaml"


class _Override(NamedTuple):
    is_trading: bool
    template: str
    reason: str


class _Phase(NamedTuple):
    start: time
    end: time
    phase: SessionPhase


class SessionTemplate(NamedTuple):
    close_time: time
    eod_freshness_minutes: int
    phases: tuple[_Phase, ...]


def _parse_time(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


@lru_cache(maxsize=1)
def _load_overrides() -> dict[date, _Override]:
    """loads exceptional holiday and weekend schedules from the seed file"""
    overrides: dict[date, _Override] = {}
    if not _CALENDAR_SEED.exists():
        return overrides
    with _CALENDAR_SEED.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row["trade_date"].strip())
            is_trading = row["is_trading_day"].strip().lower() == "true"
            template = (row.get("session_template") or "NORMAL").strip() or "NORMAL"
            overrides[day] = _Override(is_trading, template, row.get("holiday_reason", "").strip())
    return overrides


@lru_cache(maxsize=1)
def _load_templates() -> dict[str, SessionTemplate]:
    """reads daily scheduling configurations from session windows yaml"""
    doc: dict[str, Any] = yaml.safe_load(_SESSION_WINDOWS.read_text(encoding="utf-8"))
    templates: dict[str, SessionTemplate] = {}
    for name, spec in doc["templates"].items():
        phases = tuple(
            _Phase(_parse_time(start), _parse_time(end), SessionPhase(phase))
            for start, end, phase in spec["phases"]
        )
        templates[name] = SessionTemplate(_parse_time(spec["close_time"]), int(spec["eod_freshness_minutes"]), phases)
    return templates


class SessionCalendar:
    """calculates trading phases and close times for given dates"""

    def __init__(self, *, offset_hours: int = WIB_OFFSET_HOURS) -> None:
        self._offset = timedelta(hours=offset_hours)

    def is_trading_day(self, day: date) -> bool:
        override = _load_overrides().get(day)
        if override is not None:
            return override.is_trading
        return day.weekday() < 5

    def template_for(self, day: date) -> SessionTemplate:
        override = _load_overrides().get(day)
        name = override.template if override else "NORMAL"
        templates = _load_templates()
        return templates.get(name, templates["NORMAL"])

    def close_datetime_utc(self, day: date) -> datetime:
        wib_close = datetime.combine(day, self.template_for(day).close_time)
        return (wib_close - self._offset).replace(tzinfo=UTC)

    def open_datetime_utc(self, day: date) -> datetime:
        """When real trading starts, so in-session checks do not fire before the bell."""
        phases = self.template_for(day).phases
        first = next((p for p in phases if p.phase is SessionPhase.SESSION_1), phases[0])
        return (datetime.combine(day, first.start) - self._offset).replace(tzinfo=UTC)

    def eod_freshness_minutes(self, day: date) -> int:
        return self.template_for(day).eod_freshness_minutes

    def market_state(self, day: date, now_utc: datetime | None = None) -> SessionPhase:
        if not self.is_trading_day(day):
            return SessionPhase.CLOSED
        now = now_utc or datetime.now(UTC)
        wib_now = (now.astimezone(UTC) + self._offset).time()
        for window in self.template_for(day).phases:
            if window.start <= wib_now < window.end:
                return window.phase
        return SessionPhase.CLOSED


@lru_cache(maxsize=1)
def get_calendar() -> SessionCalendar:
    return SessionCalendar()

