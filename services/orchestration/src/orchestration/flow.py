"""
gloomberg_daily_flow :

guard -> ingest -> coverage gate -> dbt build -> promote 
-> agentic trigger -> finalize. Each phase is an isolated, retryable task 

a failed phase emits its event and does not silently cascade
"""

from __future__ import annotations

from datetime import date
from typing import Any

from prefect import flow
from prefect.runtime import flow_run
from prefect.task_runners import ThreadPoolTaskRunner

from pipeline.bronze.feeds import EOD_FEEDS
from pipeline.bronze.ingest import FetchError
from pipeline.config import REPO_ROOT, get_settings

from orchestration.clock import coerce_date, now_utc
from orchestration.config import OrchestrationConfig, get_config
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.phases import rollup, run_phase
from orchestration.results import PhaseResult
from orchestration.tasks.coverage import coverage_gate
from orchestration.tasks.dbt_build import DIVIDEND_PHASES, INSIGHT_PHASES, dbt_build
from orchestration.tasks.documents import extract_dividend_filings, land_dividend_attachments
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.gold_sources import land_agent_artifacts, normalize_news
from orchestration.tasks.guard import guard_trading_day
from orchestration.tasks.ingest import ingest_feed, land_failed, load_fixtures_bronze
from orchestration.tasks.project import (
    project_cash_dividend_news,
    project_corporate_actions,
    project_dividend_filings,
)
from orchestration.tasks.promote import promote_gold
from orchestration.tasks.registry import refresh_registry, retag_news
from orchestration.tasks.subjects import eod_insight_subjects
from orchestration.tasks.trigger import trigger_dividend_extraction, trigger_eod_insight


def _fixture_roots() -> list[str]:
    """The committed Bronze fixture roots, for deterministic fixture-mode ingest."""
    base = REPO_ROOT / "services" / "data-pipeline" / "fixtures"
    return [str(base / kind) for kind in ("frozen", "curated", "adversarial")]


def _ingest_live(td: date) -> list[dict[str, Any]]:
    futures = {name: ingest_feed.submit(name, td) for name in EOD_FEEDS}
    manifests: list[dict[str, Any]] = []
    for name, future in futures.items():
        try:
            manifests.append(future.result())
        except FetchError as exc:
            manifests.append(land_failed(name, td, str(exc)))
    return manifests


def _ingest_result(td: date, config: OrchestrationConfig) -> PhaseResult:
    if config.ingest_mode == "fixture":
        manifests = load_fixtures_bronze(_fixture_roots())
    else:
        manifests = _ingest_live(td)

    dated = [m for m in manifests if m["trade_date"] == td.isoformat()]
    statuses = {m["status"] for m in dated}
    status = "FAILED" if "FAILED" in statuses else ("PARTIAL" if "PARTIAL" in statuses else "SUCCESS")
    anchor = dated[0]["ingest_run_id"] if dated else None
    return PhaseResult(
        status=status,
        payload=manifests,
        notes=f"{len(manifests)} manifests landed, {len(dated)} for {td.isoformat()}",
        ingest_run_id=anchor,
    )


def _degrade_on_trigger_failure(exc: Exception) -> PhaseResult | None:
    if isinstance(exc, (TriggerTransientError, TriggerPermanentError)):
        return PhaseResult(status="DEGRADED", notes=f"agentic trigger failed: {exc}")
    return None


def _degrade_on_registry_failure(exc: Exception) -> PhaseResult | None:
    """The registry already in Postgres stays live, so the build carries on."""
    return PhaseResult(status="DEGRADED", notes=f"registry refresh failed: {exc}")


def _degrade_on_landing_failure(exc: Exception) -> PhaseResult | None:
    """A missing landing costs one day of Gold news, not the whole build."""
    return PhaseResult(status="DEGRADED", notes=f"bronze landing failed: {exc}")


def _degrade_on_projection_failure(exc: Exception) -> PhaseResult | None:
    """Unqueued corporate actions cost a day of scoring, and the next run requeues them."""
    return PhaseResult(status="DEGRADED", notes=f"corporate action projection failed: {exc}")


def _degrade_on_insight_build_failure(exc: Exception) -> PhaseResult | None:
    """The panel already serves the conclusion from Postgres, so only Gold history lags."""
    return PhaseResult(status="DEGRADED", notes=f"insight rebuild failed: {exc}")


def _dividend_extraction_result(
    dsn: str, flow_run_id: str, td: date, td_queue: PhaseResult, config: OrchestrationConfig
) -> PhaseResult:
    """Reads the day's queued filings, then folds what they declared into Gold."""
    queued = td_queue.payload if isinstance(td_queue.payload, dict) else {}
    pending = int(queued.get("pending") or 0)
    if td_queue.status != "SUCCESS" or not pending:
        skip = PhaseResult(status="SKIPPED", notes="no filing awaits extraction")
        return run_phase(dsn, flow_run_id, td, "trigger_dividend_extraction", lambda: skip)

    trigger = run_phase(
        dsn, flow_run_id, td, "trigger_dividend_extraction",
        lambda: trigger_dividend_extraction(td, config),
        on_error=_degrade_on_trigger_failure,
    )
    if trigger.status not in ("SUCCESS", "PARTIAL"):
        return trigger

    landing = run_phase(
        dsn, flow_run_id, td, "land_dividend_artifacts",
        lambda: land_agent_artifacts(td), on_error=_degrade_on_landing_failure,
    )
    build = run_phase(
        dsn, flow_run_id, td, "dbt_build_dividend",
        lambda: dbt_build(config, DIVIDEND_PHASES), on_error=_degrade_on_insight_build_failure,
    )
    promote = run_phase(
        dsn, flow_run_id, td, "promote_dividend",
        lambda: promote_gold(), on_error=_degrade_on_insight_build_failure,
    )
    feed = run_phase(
        dsn, flow_run_id, td, "project_cash_dividend_news",
        lambda: project_cash_dividend_news(td), on_error=_degrade_on_projection_failure,
    )
    return PhaseResult(
        status=rollup(trigger.status, landing.status, build.status, promote.status, feed.status),
        run_id=trigger.run_id,
        notes=f"{pending} filings read and folded into Gold",
    )


def _eod_insight_result(
    dsn: str, flow_run_id: str, td: date, config: OrchestrationConfig
) -> PhaseResult:
    """Concludes the closed session, then folds that conclusion into Gold in the same run."""
    subjects = run_phase(
        dsn, flow_run_id, td, "eod_insight_subjects",
        lambda: eod_insight_subjects(td, config.eod_insight_subject_cap, config.subject_universe),
    )
    tickers = list(subjects.payload or [])
    if not tickers:
        skip = PhaseResult(status="SKIPPED", notes=f"no conclusion owed: {subjects.notes}")
        return run_phase(dsn, flow_run_id, td, "trigger_eod_insight", lambda: skip)

    trigger = run_phase(
        dsn, flow_run_id, td, "trigger_eod_insight",
        lambda: trigger_eod_insight(td, tickers, config),
        on_error=_degrade_on_trigger_failure,
    )
    if trigger.status not in ("SUCCESS", "PARTIAL"):
        return trigger

    # only this run can land it, tomorrow lands tomorrow
    landing = run_phase(
        dsn, flow_run_id, td, "land_eod_insight",
        lambda: land_agent_artifacts(td), on_error=_degrade_on_landing_failure,
    )
    build = run_phase(
        dsn, flow_run_id, td, "dbt_build_insight",
        lambda: dbt_build(config, INSIGHT_PHASES), on_error=_degrade_on_insight_build_failure,
    )
    promote = run_phase(
        dsn, flow_run_id, td, "promote_insight",
        lambda: promote_gold(), on_error=_degrade_on_insight_build_failure,
    )
    return PhaseResult(
        status=rollup(trigger.status, landing.status, build.status, promote.status),
        run_id=trigger.run_id,
        notes=f"{len(tickers)} subjects concluded and rebuilt into Gold",
    )


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=2)


@flow(name="gloomberg_daily_flow", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def gloomberg_daily_flow(trade_date: str | None = None) -> str:
    """Runs the calendar-aware daily cycle for one WIB trade_date; returns the run status."""
    config = get_config()
    td = coerce_date(trade_date)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = now_utc()
    overall = "SUCCESS"

    try:
        guard = run_phase(dsn, flow_run_id, td, "guard", lambda: guard_trading_day(td, config))
        if not guard.payload:
            overall = "SKIPPED"
            return overall

        ingest = run_phase(dsn, flow_run_id, td, "ingest", lambda: _ingest_result(td, config))
        run_phase(
            dsn, flow_run_id, td, "refresh_registry", refresh_registry,
            on_error=_degrade_on_registry_failure,
        )
        run_phase(dsn, flow_run_id, td, "retag_news", retag_news, on_error=_degrade_on_registry_failure)
        gate = run_phase(
            dsn,
            flow_run_id,
            td,
            "coverage_gate",
            lambda: coverage_gate(ingest.payload, td, config.coverage_floor, config.coverage_hard_min),
        )
        if not gate.payload:
            overall = "FAILED"  # below hard minimum; prior Gold stays live
            return overall

        landings = [
            run_phase(
                dsn, flow_run_id, td, "normalize_news",
                lambda: normalize_news(td), on_error=_degrade_on_landing_failure,
            ),
            run_phase(
                dsn, flow_run_id, td, "land_artifacts",
                lambda: land_agent_artifacts(td), on_error=_degrade_on_landing_failure,
            ),
        ]
        run_phase(dsn, flow_run_id, td, "dbt_build", lambda: dbt_build(config))
        run_phase(dsn, flow_run_id, td, "promote", lambda: promote_gold())

        corporate_actions = run_phase(
            dsn, flow_run_id, td, "project_corporate_actions",
            lambda: project_corporate_actions(td), on_error=_degrade_on_projection_failure,
        )

        # off the path to Gold, so a flaky fetch never delays the day's prices
        dividend_documents = run_phase(
            dsn, flow_run_id, td, "land_dividend_attachments",
            lambda: land_dividend_attachments(td), on_error=_degrade_on_landing_failure,
        )
        dividend_text = run_phase(
            dsn, flow_run_id, td, "extract_dividend_filings",
            lambda: extract_dividend_filings(td), on_error=_degrade_on_landing_failure,
        )
        dividend_queue = run_phase(
            dsn, flow_run_id, td, "project_dividend_filings",
            lambda: project_dividend_filings(td), on_error=_degrade_on_projection_failure,
        )

        dividends = _dividend_extraction_result(dsn, flow_run_id, td, dividend_queue, config)

        # after promote, so it reads the closes the day settled on
        insight = _eod_insight_result(dsn, flow_run_id, td, config)

        overall = rollup(
            gate.status,
            corporate_actions.status,
            dividend_documents.status,
            dividend_text.status,
            dividend_queue.status,
            dividends.status,
            insight.status,
            *(landing.status for landing in landings),
        )
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
            notes=f"daily flow {td.isoformat()} -> {overall}",
        )


if __name__ == "__main__":
    print(gloomberg_daily_flow())
