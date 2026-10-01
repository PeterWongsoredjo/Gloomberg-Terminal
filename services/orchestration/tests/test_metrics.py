"""Pushgateway metrics: optional, grouped by flow and status, and never fatal to a run."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import orchestration.metrics as metrics_mod
import orchestration.phases as phases_mod
import orchestration.tasks.finalize as finalize_mod
import orchestration.tasks.quarantine as quarantine_mod
from orchestration.results import PhaseResult

TD = date(2026, 7, 15)
ENDED = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
STARTED = ENDED - timedelta(seconds=42)


@pytest.fixture(autouse=True)
def _fresh_counts() -> Any:
    metrics_mod._stage_records.clear()
    yield
    metrics_mod._stage_records.clear()


def _capture_pushes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    pushes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        metrics_mod, "push_to_gateway", lambda url, **kw: pushes.append({"url": url, **kw})
    )
    return pushes


def test_no_url_means_no_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monitoring is opt in, an unset gateway must cost nothing."""
    monkeypatch.setenv("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "")
    pushes = _capture_pushes(monkeypatch)
    assert metrics_mod.push_run("intraday_news", "SKIPPED", STARTED, ENDED) is False
    assert pushes == []


def test_a_push_is_grouped_by_flow_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Separate groups are what keep the last SUCCESS alive after a FAILED run."""
    monkeypatch.setenv("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "http://gateway:9091")
    pushes = _capture_pushes(monkeypatch)
    metrics_mod.record_stage("ingest", PhaseResult(status="SUCCESS", records_processed=957))
    metrics_mod.record_stage("count_quarantine", PhaseResult(status="SUCCESS", records_rejected=3))

    assert metrics_mod.push_run("gloomberg_daily_flow", "PARTIAL", STARTED, ENDED) is True

    (push,) = pushes
    assert push["url"] == "http://gateway:9091"
    assert push["job"] == metrics_mod.JOB
    assert push["grouping_key"] == {"flow": "gloomberg_daily_flow", "status": "PARTIAL"}
    registry = push["registry"]
    assert registry.get_sample_value("gloomberg_pipeline_last_run_duration_seconds") == 42.0
    assert registry.get_sample_value(
        "gloomberg_pipeline_last_run_finished_timestamp_seconds"
    ) == ENDED.timestamp()
    assert registry.get_sample_value("gloomberg_pipeline_records_processed", {"stage": "ingest"}) == 957
    assert registry.get_sample_value(
        "gloomberg_pipeline_records_rejected", {"stage": "count_quarantine"}
    ) == 3


def test_an_unreachable_gateway_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real push to a closed port fails quietly and still clears the run's counts."""
    monkeypatch.setenv("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "http://127.0.0.1:1")
    metrics_mod.record_stage("ingest_news", PhaseResult(status="SUCCESS", records_processed=12))

    assert metrics_mod.push_run("intraday_news", "SUCCESS", STARTED, ENDED) is False
    assert metrics_mod._stage_records == {}


def test_counts_never_leak_into_the_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "http://gateway:9091")
    pushes = _capture_pushes(monkeypatch)
    metrics_mod.record_stage("ingest", PhaseResult(status="SUCCESS", records_processed=5))
    metrics_mod.push_run("gloomberg_daily_flow", "SUCCESS", STARTED, ENDED)
    metrics_mod.push_run("gloomberg_daily_flow", "SKIPPED", STARTED, ENDED)

    second = pushes[1]["registry"]
    assert second.get_sample_value("gloomberg_pipeline_records_processed", {"stage": "ingest"}) is None


def test_run_phase_records_counts_on_success_and_on_a_handled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(phases_mod, "emit_phase", lambda *a, **k: None)
    phases_mod.run_phase(
        "dsn", "fr", TD, "ingest", lambda: PhaseResult(status="SUCCESS", records_processed=7)
    )

    def broken() -> PhaseResult:
        raise RuntimeError("duckdb gone")

    phases_mod.run_phase(
        "dsn", "fr", TD, "count_quarantine", broken,
        on_error=lambda exc: PhaseResult(status="DEGRADED", notes=str(exc)),
    )

    assert metrics_mod._stage_records == {("ingest", "processed"): 7}


def test_finalize_survives_a_dead_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """The event still lands and finalize returns, whatever monitoring is doing."""
    monkeypatch.setenv("GLOOMBERG_ORCH_PUSHGATEWAY_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(finalize_mod, "sink_event", lambda event, dsn: True)
    seen: dict[str, Any] = {}
    real_push = metrics_mod.push_run

    def spy(flow: str, status: str, started_at: datetime, ended_at: datetime) -> bool:
        seen.update(flow=flow, status=status)
        return real_push(flow, status, started_at, ended_at)

    monkeypatch.setattr(finalize_mod, "push_run", spy)

    assert finalize_mod.finalize_run(
        dsn="dsn", flow_run_id="local", trade_date=TD, overall_status="FAILED",
        started_at=STARTED, notes="test",
    ) is True
    assert seen == {"flow": "unknown", "status": "FAILED"}


def test_quarantine_count_becomes_rejected_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quarantine_mod, "quarantined_rows", lambda path, td: 4)
    result = quarantine_mod.count_quarantine(TD)
    assert result.status == "SUCCESS"
    assert result.records_rejected == 4


def test_an_unreadable_quarantine_count_only_degrades() -> None:
    degraded = quarantine_mod.degrade_on_count_failure(RuntimeError("no snapshot"))
    assert degraded is not None and degraded.status == "DEGRADED"
