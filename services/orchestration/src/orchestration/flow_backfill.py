"""
backfill_sentiment :
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from prefect import flow
from prefect.runtime import flow_run
from prefect.task_runners import ThreadPoolTaskRunner

from pipeline.config import get_settings

from orchestration.clock import coerce_date, now_utc
from orchestration.config import OrchestrationConfig, get_config
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.phases import rollup, run_phase
from orchestration.projection import pending_count
from orchestration.results import PhaseResult
from orchestration.tasks.dbt_build import dbt_build
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.gold_sources import reconcile_agent_artifacts, reconcile_news_items
from orchestration.tasks.promote import promote_gold
from orchestration.tasks.trigger import trigger_intraday

BACKFILL_WINDOW_DAYS = 14

MAX_ROUNDS = 4


def _degrade_on_trigger_failure(exc: Exception) -> PhaseResult | None:
    if isinstance(exc, (TriggerTransientError, TriggerPermanentError)):
        return PhaseResult(status="DEGRADED", notes=f"backfill trigger failed: {exc}")
    return None


def backfill_dates(today: date, window_days: int = BACKFILL_WINDOW_DAYS) -> list[date]:
    """The retained window, oldest first, so the feed fills in from the back."""
    return [today - timedelta(days=n) for n in range(window_days, -1, -1)]


def _drain_date(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig, max_rounds: int
) -> tuple[int, str]:
    """Re-dispatches one date until its backlog stops shrinking or the rounds run out."""
    scored = 0
    status = "SKIPPED"
    for _ in range(max_rounds):
        before = pending_count(dsn, td)
        if before == 0:
            break
        result = run_phase(
            dsn, flow_run_id, td, "backfill_score",
            lambda: trigger_intraday(td, [], config),
            on_error=_degrade_on_trigger_failure,
        )
        status = result.status
        after = pending_count(dsn, td)
        scored += max(0, before - after)
        if after >= before or status == "DEGRADED":
            break
    return scored, status


def _degrade_on_recovery_failure(exc: Exception) -> PhaseResult | None:
    """Yesterday's leftovers are worth a retry tomorrow, never a failed run."""
    return PhaseResult(status="DEGRADED", notes=f"recovery failed: {exc}")


def _recover_missed_days(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig
) -> list[str]:
    """Lands whatever earlier runs left behind, then rebuilds Gold if anything landed."""
    recovered = [
        run_phase(
            dsn, flow_run_id, td, "reconcile_news_items",
            lambda: reconcile_news_items(), on_error=_degrade_on_recovery_failure,
        ),
        run_phase(
            dsn, flow_run_id, td, "reconcile_agent_artifacts",
            lambda: reconcile_agent_artifacts(), on_error=_degrade_on_recovery_failure,
        ),
    ]
    statuses = [r.status for r in recovered if r.status != "SKIPPED"]
    if not any(r.status == "SUCCESS" for r in recovered):
        return statuses

    # nothing new reached Bronze on a skip, so there is nothing to rebuild
    build = run_phase(
        dsn, flow_run_id, td, "dbt_build",
        lambda: dbt_build(config), on_error=_degrade_on_recovery_failure,
    )
    promote = run_phase(
        dsn, flow_run_id, td, "promote",
        lambda: promote_gold(), on_error=_degrade_on_recovery_failure,
    )
    return [*statuses, build.status, promote.status]


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=1)


@flow(name="backfill_sentiment", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def backfill_sentiment_flow(trade_date: str | None = None, max_rounds: int = MAX_ROUNDS) -> str:
    """Scores whatever is still unscored across the retained window; returns the run status."""
    config = get_config()
    td = coerce_date(trade_date)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = now_utc()
    overall = "SKIPPED"
    scored = 0

    try:
        statuses = []
        for day in backfill_dates(td):
            drained, status = _drain_date(dsn, flow_run_id, day, config, max_rounds)
            scored += drained
            if status != "SKIPPED":
                statuses.append(status)
        statuses.extend(_recover_missed_days(dsn, flow_run_id, td, config))
        overall = rollup(*statuses) if statuses else "SKIPPED"
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
            notes=f"backfill scored {scored} articles -> {overall}",
        )


if __name__ == "__main__":
    print(backfill_sentiment_flow())
