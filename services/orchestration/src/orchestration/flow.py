"""
gloomberg_daily_flow :

guard -> ingest -> coverage gate -> dbt build -> promote 
-> agentic trigger -> finalize. Each phase is an isolated, retryable task 

a failed phase emits its event and does not silently cascade
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from prefect import flow
from prefect.runtime import flow_run
from prefect.task_runners import ThreadPoolTaskRunner

from pipeline.bronze.feeds import EOD_FEEDS
from pipeline.bronze.ingest import FetchError
from pipeline.config import REPO_ROOT, get_settings

from orchestration.config import OrchestrationConfig, get_config
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.events import build_event, sink_event
from orchestration.results import PhaseResult
from orchestration.tasks.coverage import coverage_gate
from orchestration.tasks.dbt_build import dbt_build
from orchestration.tasks.finalize import finalize_run
from orchestration.tasks.guard import guard_trading_day
from orchestration.tasks.ingest import ingest_feed, land_failed, load_fixtures_bronze
from orchestration.tasks.promote import promote_gold
from orchestration.tasks.trigger import trigger_agentic

_SEVERITY = {"SUCCESS": 0, "SKIPPED": 0, "PARTIAL": 1, "DEGRADED": 2, "FAILED": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wib_today() -> date:
    return (_now() + timedelta(hours=7)).date()


def _coerce_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else _wib_today()


def _fixture_roots() -> list[str]:
    """The committed Bronze fixture roots, for deterministic fixture-mode ingest."""
    base = REPO_ROOT / "services" / "data-pipeline" / "fixtures"
    return [str(base / kind) for kind in ("frozen", "curated", "adversarial")]


def _emit(dsn: str, flow_run_id: str, td: date, phase: str, result: PhaseResult, started: datetime) -> None:
    event = build_event(
        flow_run_id=flow_run_id,
        trade_date=td,
        phase=phase,
        status=result.status,
        started_at=started,
        ended_at=_now(),
        ingest_run_id=result.ingest_run_id,
        dbt_invocation_id=result.dbt_invocation_id,
        run_id=result.run_id,
        gate=result.gate,
        notes=result.notes,
    )
    sink_event(event, dsn)


def _run_phase(
    dsn: str,
    flow_run_id: str,
    td: date,
    phase: str,
    fn: Callable[[], PhaseResult],
    on_error: Callable[[Exception], PhaseResult | None] | None = None,
) -> PhaseResult:
    started = _now()
    try:
        result = fn()
    except Exception as exc:
        handled = on_error(exc) if on_error else None
        if handled is None:
            _emit(dsn, flow_run_id, td, phase, PhaseResult(status="FAILED", notes=str(exc)), started)
            raise
        result = handled
    _emit(dsn, flow_run_id, td, phase, result, started)
    return result


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


def _rollup(*statuses: str) -> str:
    """Overall run status is the most severe among the phases that ran."""
    return max(statuses, key=lambda s: _SEVERITY.get(s, 0))


_TASK_RUNNER: ThreadPoolTaskRunner[Any] = ThreadPoolTaskRunner(max_workers=2)


@flow(name="gloomberg_daily_flow", task_runner=_TASK_RUNNER)  # type: ignore[arg-type]
def gloomberg_daily_flow(trade_date: str | None = None, objective: str | None = None) -> str:
    """Runs the calendar-aware daily cycle for one WIB trade_date; returns the run status."""
    config = get_config()
    if objective:
        config = replace(config, objective=objective)
    td = _coerce_date(trade_date)
    dsn = get_settings().postgres_dsn
    flow_run_id = str(flow_run.id) if flow_run.id else "local"
    started = _now()
    overall = "SUCCESS"

    try:
        guard = _run_phase(dsn, flow_run_id, td, "guard", lambda: guard_trading_day(td, config))
        if not guard.payload:
            overall = "SKIPPED"
            return overall

        ingest = _run_phase(dsn, flow_run_id, td, "ingest", lambda: _ingest_result(td, config))
        gate = _run_phase(
            dsn,
            flow_run_id,
            td,
            "coverage_gate",
            lambda: coverage_gate(ingest.payload, td, config.coverage_floor, config.coverage_hard_min),
        )
        if not gate.payload:
            overall = "FAILED"  # below hard minimum; prior Gold stays live
            return overall

        _run_phase(dsn, flow_run_id, td, "dbt_build", lambda: dbt_build(config))
        _run_phase(dsn, flow_run_id, td, "promote", lambda: promote_gold())
        trigger = _run_phase(
            dsn, flow_run_id, td, "trigger_agentic",
            lambda: trigger_agentic(td, config), on_error=_degrade_on_trigger_failure,
        )

        overall = _rollup(gate.status, trigger.status)
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
