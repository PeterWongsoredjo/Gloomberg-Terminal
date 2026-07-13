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
