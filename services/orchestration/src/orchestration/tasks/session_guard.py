"""
Is the market open right now? Anything but an open session is a SKIP, never a failure.
"""

from __future__ import annotations

from datetime import date, datetime

from prefect import task

from orchestration.calendar import OPEN_SESSIONS, holiday_reason, is_trading_day, session_phase
from orchestration.config import OrchestrationConfig
from orchestration.results import PhaseResult


@task(name="guard_open_session")
def guard_open_session(trade_date: date, now: datetime, config: OrchestrationConfig) -> PhaseResult:
    if not is_trading_day(trade_date, config.calendar_seed):
        reason = holiday_reason(trade_date, config.calendar_seed)
        return PhaseResult(status="SKIPPED", payload=False, notes=f"non-trading day: {reason}")
    phase = session_phase(trade_date, now, config.calendar_seed, config.session_windows)
    if phase is None:
        return PhaseResult(status="SKIPPED", payload=False, notes="session windows unresolved, failing closed")
    if phase not in OPEN_SESSIONS:
        return PhaseResult(status="SKIPPED", payload=False, notes=f"outside session: {phase}")
    return PhaseResult(status="SUCCESS", payload=True, notes=f"market open: {phase}")
