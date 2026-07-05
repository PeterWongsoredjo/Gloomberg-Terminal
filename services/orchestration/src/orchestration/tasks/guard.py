"""OR-01 phase 1: is this a trading day? A closed session is a SKIP, never a failure."""

from __future__ import annotations

from datetime import date

from prefect import task

from orchestration.calendar import holiday_reason, is_trading_day
from orchestration.config import OrchestrationConfig
from orchestration.results import PhaseResult


@task(name="guard_trading_day")
def guard_trading_day(trade_date: date, config: OrchestrationConfig) -> PhaseResult:
    """Passes on a trading day; SKIPS (payload False) on a weekend or holiday."""
    if is_trading_day(trade_date, config.calendar_seed):
        return PhaseResult(status="SUCCESS", payload=True, notes="trading day")
    reason = holiday_reason(trade_date, config.calendar_seed)
    return PhaseResult(status="SKIPPED", payload=False, notes=f"non-trading day: {reason}")
