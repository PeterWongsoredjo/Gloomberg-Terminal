"""
Holidays and working-Saturday are exceptions in the orchestration
this file dictates that our prefect only runs on days where ihsg is open
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple

import yaml

# the phases during which the intraday poller is allowed to run
OPEN_SESSIONS = frozenset({"SESSION_1", "SESSION_2"})


class _Override(NamedTuple):
    is_trading: bool
    reason: str
    template: str


class SessionWindow(NamedTuple):
    start: time
    end: time
    phase: str


def _load_overrides(calendar_seed: Path) -> dict[date, _Override]:
    """Reads the explicit is_trading_day / reason exceptions from the calendar seed once."""
    overrides: dict[date, _Override] = {}
    if not calendar_seed.exists():
        return overrides
    with calendar_seed.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row["trade_date"].strip())
            is_trading = row["is_trading_day"].strip().lower() == "true"
            template = (row.get("session_template") or "").strip() or "NORMAL"
            overrides[day] = _Override(is_trading, row.get("holiday_reason", "").strip(), template)
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


def session_template_name(day: date, calendar_seed: Path) -> str:
    """The session template the calendar assigns this day, NORMAL by default."""
    override = _load_overrides(calendar_seed).get(day)
    return override.template if override else "NORMAL"


def load_session_windows(path: Path) -> tuple[int, dict[str, list[SessionWindow]]] | None:
    """Reads the WIB offset and phase windows per template, None when unreadable."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        offset = int(doc["timezone_offset_hours"])
        templates: dict[str, list[SessionWindow]] = {}
        for name, spec in doc["templates"].items():
            windows = [
                SessionWindow(time.fromisoformat(start), time.fromisoformat(end), str(phase))
                for start, end, phase in spec["phases"]
            ]
            templates[str(name)] = windows
        return offset, templates
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def session_phase(day: date, now: datetime, calendar_seed: Path, windows_path: Path) -> str | None:
    """The market phase at a UTC instant, None when config cannot be resolved."""
    loaded = load_session_windows(windows_path)
    if loaded is None:
        return None
    offset, templates = loaded
    windows = templates.get(session_template_name(day, calendar_seed))
    if windows is None:
        return None
    local = (now + timedelta(hours=offset)).time()
    for window in windows:
        if window.start <= local < window.end:
            return window.phase
    return "CLOSED"
