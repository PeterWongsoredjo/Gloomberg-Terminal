from __future__ import annotations

from datetime import date, datetime, timezone

from prefect.runtime import flow_run

from orchestration.events import build_event, sink_event
from orchestration.metrics import push_run


def finalize_run(
    *,
    dsn: str,
    flow_run_id: str,
    trade_date: date,
    overall_status: str,
    started_at: datetime,
    notes: str,
) -> bool:
    ended_at = datetime.now(timezone.utc)
    event = build_event(
        flow_run_id=flow_run_id,
        trade_date=trade_date,
        phase="finalize",
        status=overall_status,
        started_at=started_at,
        ended_at=ended_at,
        notes=notes,
    )
    sunk = sink_event(event, dsn)
    push_run(flow_run.flow_name or "unknown", overall_status, started_at, ended_at)
    return sunk
