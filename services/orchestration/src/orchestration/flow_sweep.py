"""
idx_resweep :

find the day's blocked feeds -> fetch them again -> rebuild Gold only if one landed.

The proxy blocks in stretches, so an hour later is a different roll of the dice.
A day with nothing outstanding costs one object listing and stops.
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
from orchestration.phases import rollup, run_phase
from orchestration.results import PhaseResult
from orchestration.tasks.dbt_build import dbt_build
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.promote import promote_gold
from orchestration.tasks.sweep import (
    coverage_recovered,
    recovered_feeds,
    resweep_dividend_documents,
    resweep_feeds,
)


def _degrade_on_rebuild_failure(exc: Exception) -> PhaseResult | None:
    """The bytes are safely in Bronze, so tomorrow's build picks them up regardless."""
    return PhaseResult(status="DEGRADED", notes=f"resweep rebuild failed: {exc}")


def _rebuild_needed(recovered: list[str], coverage_ok: bool) -> bool:
    """New bytes alone never ship; the gate has to say the day may be promoted."""
    return bool(recovered) and coverage_ok


def _rebuild_result(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig
) -> PhaseResult:
    """Folds a recovered universe feed into Gold, the same way the daily run would."""
    build = run_phase(
        dsn, flow_run_id, td, "resweep_dbt_build",
        lambda: dbt_build(config), on_error=_degrade_on_rebuild_failure,
    )
    promote = run_phase(
        dsn, flow_run_id, td, "resweep_promote",
        lambda: promote_gold(), on_error=_degrade_on_rebuild_failure,
    )
    return PhaseResult(status=rollup(build.status, promote.status), notes="recovered prices rebuilt")


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=2)


@flow(name="idx_resweep", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def idx_resweep_flow(trade_date: str | None = None) -> str:
    """Retries the day's blocked IDX fetches; returns the run status."""
    config = get_config()
    td = coerce_date(trade_date)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = now_utc()
    overall = "SUCCESS"

    try:
        feeds = run_phase(dsn, flow_run_id, td, "resweep_feeds", lambda: resweep_feeds(td))
        documents = run_phase(
            dsn, flow_run_id, td, "resweep_dividend_documents",
            lambda: resweep_dividend_documents(td),
        )

        recovered = recovered_feeds(feeds)
        if _rebuild_needed(recovered, coverage_recovered(feeds)):
            rebuild = _rebuild_result(dsn, flow_run_id, td, config)
        else:
            skip = PhaseResult(status="SKIPPED", notes="nothing recovered that changes Gold")
            rebuild = run_phase(dsn, flow_run_id, td, "resweep_dbt_build", lambda: skip)

        overall = rollup(feeds.status, documents.status, rebuild.status)
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
            notes=f"idx resweep {td.isoformat()} -> {overall}",
        )


if __name__ == "__main__":
    print(idx_resweep_flow())
