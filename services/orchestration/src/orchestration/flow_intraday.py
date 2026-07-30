"""
intraday_news :

session guard -> land the curated RSS feeds -> project new items to Postgres
-> trigger intraday sentiment when anything new was tagged -> finalize.

Outside an open session the whole run is a SKIP, never a failure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from prefect import flow
from prefect.runtime import flow_run
from prefect.task_runners import ThreadPoolTaskRunner

from pipeline.bronze.feeds import NEWS_FEEDS
from pipeline.bronze.ingest import FetchError
from pipeline.config import get_settings

from orchestration.clock import coerce_date, now_utc
from orchestration.config import OrchestrationConfig, get_config
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.phases import rollup, run_phase
from orchestration.results import PhaseResult
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.ingest import ingest_feed, land_failed
from orchestration.tasks.project import project_news
from orchestration.tasks.session_guard import guard_open_session
from orchestration.tasks.trigger import trigger_intraday


def ingest_news_result(manifests: list[dict[str, Any]]) -> PhaseResult:
    """Feed flap tolerance: only every feed failing fails the phase."""
    landed = [m for m in manifests if m.get("status") != "FAILED"]
    if len(landed) == len(manifests):
        status = "SUCCESS"
    elif landed:
        status = "PARTIAL"
    else:
        status = "FAILED"
    anchor = landed[0]["ingest_run_id"] if landed else None
    return PhaseResult(
        status=status,
        payload=manifests,
        notes=f"{len(landed)}/{len(manifests)} news feeds landed",
        ingest_run_id=anchor,
    )


def _ingest_news(td: date) -> PhaseResult:
    futures = {name: ingest_feed.submit(name, td) for name in NEWS_FEEDS}
    manifests: list[dict[str, Any]] = []
    for name, future in futures.items():
        try:
            manifests.append(future.result())
        except FetchError as exc:
            manifests.append(land_failed(name, td, str(exc)))
    return ingest_news_result(manifests)


def pending_articles(payload: dict[str, Any] | None) -> int:
    """How many of the day's articles still need scoring after this poll."""
    if not payload:
        return 0
    return int(payload.get("pending") or 0)


def _degrade_on_projection_failure(exc: Exception) -> PhaseResult | None:
    return PhaseResult(status="DEGRADED", notes=f"projection write failed: {exc}")


def _degrade_on_trigger_failure(exc: Exception) -> PhaseResult | None:
    if isinstance(exc, (TriggerTransientError, TriggerPermanentError)):
        return PhaseResult(status="DEGRADED", notes=f"intraday trigger failed: {exc}")
    return None


def _trigger_result(
    dsn: str, flow_run_id: str, td: date, project: PhaseResult, config: OrchestrationConfig
) -> PhaseResult:
    """Triggers scoring whenever any article is still unscored, universe or not."""
    if project.status != "SUCCESS":
        skip = PhaseResult(status="SKIPPED", notes="projection unavailable, scoring skipped")
        return run_phase(dsn, flow_run_id, td, "trigger_intraday", lambda: skip)
    pending = pending_articles(project.payload)
    if not pending:
        skip = PhaseResult(status="SKIPPED", notes="no unscored articles, trigger skipped")
        return run_phase(dsn, flow_run_id, td, "trigger_intraday", lambda: skip)
    return run_phase(
        dsn, flow_run_id, td, "trigger_intraday",
        lambda: trigger_intraday(td, [], config), on_error=_degrade_on_trigger_failure,
    )


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=2)


@flow(name="intraday_news", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def intraday_news_flow(trade_date: str | None = None) -> str:
    """Runs one in-session news poll for a WIB trade_date; returns the run status."""
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

        ingest = run_phase(dsn, flow_run_id, td, "ingest_news", lambda: _ingest_news(td))
        # a failed fetch still projects, an earlier poll may have unprojected items
        project = run_phase(
            dsn, flow_run_id, td, "project_news",
            lambda: project_news(td, config, ingest.ingest_run_id),
            on_error=_degrade_on_projection_failure,
        )
        trigger = _trigger_result(dsn, flow_run_id, td, project, config)

        overall = rollup(ingest.status, project.status, trigger.status)
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
            notes=f"intraday news poll {td.isoformat()} -> {overall}",
        )


if __name__ == "__main__":
    print(intraday_news_flow())
