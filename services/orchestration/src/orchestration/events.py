"""OR-06 run-state events: the phase-by-phase record the observability plane (05) consumes.

We own the emit side. Events carry the OB-01 correlation anchors so an operator can pivot
from a Data Telemetry figure back to the flow run that produced it. The sink is best-effort:
a down Postgres never blocks the workload (05 1.3) — emission failures are swallowed and logged.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import psycopg

from pipeline.bronze.manifest import iso_utc

SCHEMA_VERSION = "1.0.0"

_DDL = """
create schema if not exists orchestration;
create table if not exists orchestration.run_state_event (
    id bigserial primary key,
    flow_run_id text,
    trade_date date,
    phase text not null,
    status text not null,
    started_at timestamptz,
    ended_at timestamptz,
    event jsonb not null,
    recorded_at timestamptz not null default now()
);
"""


_schema_ready = False


def build_event(
    *,
    flow_run_id: str,
    trade_date: date,
    phase: str,
    status: str,
    started_at: datetime,
    ended_at: datetime,
    ingest_run_id: str | None = None,
    dbt_invocation_id: str | None = None,
    run_id: str | None = None,
    gate: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Assembles one OR-06 run-state event with its OB-01 correlation anchors."""
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "flow_run_id": flow_run_id,
        "trade_date": trade_date.isoformat(),
        "phase": phase,
        "status": status,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(ended_at),
        "correlation": {
            "ingest_run_id": ingest_run_id,
            "dbt_invocation_id": dbt_invocation_id,
            "run_id": run_id,
        },
        "notes": notes,
    }
    if gate is not None:
        event["gate"] = gate
    return event


def sink_event(event: dict[str, Any], dsn: str) -> bool:
    """Writes one event to Postgres; returns False on failure, never raises. Bounded by a
    connect timeout so a down Postgres degrades emission without stalling the workload."""
    global _schema_ready
    try:
        # a hard connect timeout keeps a slow/down Postgres from blocking the workload
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            if not _schema_ready:
                conn.execute(_DDL)  # idempotent; run once per process, not per event
                _schema_ready = True
            conn.execute(
                """
                insert into orchestration.run_state_event
                    (flow_run_id, trade_date, phase, status, started_at, ended_at, event)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event["flow_run_id"],
                    event["trade_date"],
                    event["phase"],
                    event["status"],
                    event["started_at"],
                    event["ended_at"],
                    json.dumps(event),
                ),
            )
        return True
    except Exception:  # observability emission is best-effort; the workload must not block
        return False
