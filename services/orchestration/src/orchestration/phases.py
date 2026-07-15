"""Shared phase plumbing: run a phase, emit its event, roll statuses up."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from orchestration.clock import now_utc
from orchestration.events import build_event, sink_event
from orchestration.results import PhaseResult

SEVERITY = {"SUCCESS": 0, "SKIPPED": 0, "PARTIAL": 1, "DEGRADED": 2, "FAILED": 3}


def emit_phase(dsn: str, flow_run_id: str, td: date, phase: str, result: PhaseResult, started: datetime) -> None:
    event = build_event(
        flow_run_id=flow_run_id,
        trade_date=td,
        phase=phase,
        status=result.status,
        started_at=started,
        ended_at=now_utc(),
        ingest_run_id=result.ingest_run_id,
        dbt_invocation_id=result.dbt_invocation_id,
        run_id=result.run_id,
        gate=result.gate,
        notes=result.notes,
    )
    sink_event(event, dsn)


def run_phase(
    dsn: str,
    flow_run_id: str,
    td: date,
    phase: str,
    fn: Callable[[], PhaseResult],
    on_error: Callable[[Exception], PhaseResult | None] | None = None,
) -> PhaseResult:
    """Times one phase, emits its event, and lets on_error downgrade a raise."""
    started = now_utc()
    try:
        result = fn()
    except Exception as exc:
        handled = on_error(exc) if on_error else None
        if handled is None:
            emit_phase(dsn, flow_run_id, td, phase, PhaseResult(status="FAILED", notes=str(exc)), started)
            raise
        result = handled
    emit_phase(dsn, flow_run_id, td, phase, result, started)
    return result


def rollup(*statuses: str) -> str:
    """Overall run status is the most severe among the phases that ran."""
    return max(statuses, key=lambda s: SEVERITY.get(s, 0))
