"""OR-01 phase 8: emit the terminal run-state event so a run is observable even when it failed."""

from __future__ import annotations

from datetime import date, datetime, timezone

from orchestration.events import build_event, sink_event


def finalize_run(
    *,
    dsn: str,
    flow_run_id: str,
    trade_date: date,
    overall_status: str,
    started_at: datetime,
    notes: str,
) -> bool:
    """Builds and sinks the finalize event; best-effort, never raises (05 1.3)."""
    event = build_event(
        flow_run_id=flow_run_id,
        trade_date=trade_date,
        phase="finalize",
        status=overall_status,
        started_at=started_at,
        ended_at=datetime.now(timezone.utc),
        notes=notes,
    )
    return sink_event(event, dsn)
