"""
idx_retro_backfill :

manual only, never scheduled. re-judge a past window against a later company list
-> rebuild Gold once if any day passed. every day it lets through is RETRO_AUTHORITY.
"""

from __future__ import annotations

from datetime import date

from prefect import flow
from prefect.runtime import flow_run

from pipeline.config import get_settings

from orchestration.clock import now_utc, wib_today
from orchestration.config import get_config
from orchestration.phases import rollup, run_phase
from orchestration.rebuild import rebuild_gold
from orchestration.results import PhaseResult
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.retro import retro_coverage_gate, retro_window


def _judge(dsn: str, flow_run_id: str, td: date) -> PhaseResult:
    """Runs one past day through the retro gate as its own recorded phase."""
    return run_phase(dsn, flow_run_id, td, "retro_coverage_gate", lambda: retro_coverage_gate(td))


@flow(name="idx_retro_backfill")
def idx_retro_backfill_flow(date_from: str, date_to: str) -> str:
    """Re-judges past trading days with a later capture allowed; returns the run status."""
    config = get_config()
    first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = now_utc()
    overall = "SUCCESS"
    passed: list[date] = []
    days: list[date] = []

    try:
        days = retro_window(first, last, wib_today(), config.calendar_seed)
        verdicts = [_judge(dsn, flow_run_id, td) for td in days]
        passed = [td for td, verdict in zip(days, verdicts) if verdict.payload]

        if passed:
            rebuild = rebuild_gold(dsn, flow_run_id, last, config, "retro")
        else:
            skip = PhaseResult(status="SKIPPED", notes="no day in the window passed the gate")
            rebuild = run_phase(dsn, flow_run_id, last, "retro_dbt_build", lambda: skip)

        overall = rollup(*(verdict.status for verdict in verdicts), rebuild.status)
        return overall
    except Exception:
        overall = "FAILED"
        raise
    finally:
        finalize_run(
            dsn=dsn,
            flow_run_id=flow_run_id,
            trade_date=last,
            overall_status=overall,
            started_at=started,
            notes=(
                f"retro backfill {first.isoformat()}..{last.isoformat()}: "
                f"{len(passed)} of {len(days)} days passed -> {overall}"
            ),
        )
