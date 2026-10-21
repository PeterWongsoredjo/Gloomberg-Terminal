"""
Re-judges past days against a company list captured after them, for a manual backfill.

The daily gate refuses any membership older than the day before, and IDX only ever
serves today's list, so a day nobody captured in time can never pass it again. This
is the one deliberate exception: a later capture, rewound with the listing events,
and a RETRO_AUTHORITY flag on every number it lets through.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from prefect import task

from pipeline.bronze.core.store import client
from pipeline.config import get_settings

from orchestration.calendar import is_trading_day
from orchestration.config import get_config
from orchestration.results import PhaseResult
from orchestration.tasks.coverage import run_coverage_gate, universe_manifests


def retro_window(first: date, last: date, today: date, calendar_seed: Path) -> list[date]:
    """The trading days to re-judge, oldest first, refusing anything not in the past."""
    if first > last:
        raise ValueError(f"backfill window is backwards: {first.isoformat()} after {last.isoformat()}")
    if last >= today:
        raise ValueError(f"backfill must end before today {today.isoformat()}, got {last.isoformat()}")
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return [day for day in days if is_trading_day(day, calendar_seed)]


@task(name="retro_coverage_gate")
def retro_coverage_gate(trade_date: date) -> PhaseResult:
    """Runs one past day through the shared gate, with a later capture allowed."""
    config = get_config()
    minio = client(get_settings())
    return run_coverage_gate(
        universe_manifests(minio, trade_date),
        trade_date,
        config.coverage_floor,
        config.coverage_hard_min,
        config.universe_min_securities,
        config.calendar_seed,
        retro=True,
    )
