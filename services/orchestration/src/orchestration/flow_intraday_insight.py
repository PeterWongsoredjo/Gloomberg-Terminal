"""
intraday_insight :

session guard -> trigger one insight run over the full curated universe -> finalize.

A periodic refresh, never news-triggered: every open-session tick dispatches
unconditionally. Outside an open session the whole run is a SKIP, never a failure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from prefect import flow
from prefect.runtime import flow_run
from prefect.task_runners import ThreadPoolTaskRunner

from pipeline.config import get_settings

from orchestration.clock import coerce_date, now_utc
from orchestration.config import OrchestrationConfig, get_config
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.phases import rollup, run_phase
from orchestration.results import PhaseResult
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.session_guard import guard_open_session
from orchestration.tasks.trigger import trigger_intraday_insight


def _degrade_on_trigger_failure(exc: Exception) -> PhaseResult | None:
    if isinstance(exc, (TriggerTransientError, TriggerPermanentError)):
        return PhaseResult(status="DEGRADED", notes=f"insight trigger failed: {exc}")
    return None


def _trigger_result(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig
) -> PhaseResult:
    """Dispatches the hourly insight run, or skips visibly when no universe is curated."""
    if not config.subject_universe:
        skip = PhaseResult(status="SKIPPED", notes="universe empty, insight trigger skipped")
        return run_phase(dsn, flow_run_id, td, "trigger_insight", lambda: skip)
    return run_phase(
        dsn, flow_run_id, td, "trigger_insight",
        lambda: trigger_intraday_insight(td, config.subject_universe, config),
        on_error=_degrade_on_trigger_failure,
    )


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=2)


@flow(name="intraday_insight", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def intraday_insight_flow(trade_date: str | None = None) -> str:
    """Runs one in-session insight refresh for a WIB trade_date; returns the run status."""
    config = get_config()
    td = coerce_date(trade_date)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = now_utc()
    overall = "SUCCESS"

    try:
        guard = run_phase(
            dsn, flow_run_id, td, "session_guard",
            lambda: guard_open_session(td, now_utc(), config),
        )
        if not guard.payload:
            overall = "SKIPPED"
            return overall

        trigger = _trigger_result(dsn, flow_run_id, td, config)
        overall = rollup(trigger.status)
        return overall
    except Exception:
        overall = "FAILED"
        raise
    finally:
        finalize_run(
            dsn=dsn,
            flow_run_id=flow_run_id,
            trade_date=td,
            overall_status=overall,
            started_at=started,
            notes=f"intraday insight refresh {td.isoformat()} -> {overall}",
        )


if __name__ == "__main__":
    print(intraday_insight_flow())
