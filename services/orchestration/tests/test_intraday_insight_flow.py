from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow_intraday_insight as insight_mod
from orchestration.config import OrchestrationConfig
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.results import PhaseResult

TD = date(2026, 7, 15)


def _config(tmp_path: Path, universe: list[str]) -> OrchestrationConfig:
    return OrchestrationConfig(
        dbt_dir=tmp_path,
        calendar_seed=tmp_path / "cal.csv",
        coverage_floor=0.95,
        coverage_hard_min=0.80,
        ingest_mode="live",
        backend_api_url="http://test",
        backend_api_token="",
        poll_interval_seconds=0.0,
        poll_timeout_seconds=1.0,
        trigger_timeout_seconds=1.0,
        eod_cron="0 17 * * 1-5",
        subject_universe=universe,
    )


def _passthrough_run_phase(
    dsn: str,
    flow_run_id: str,
    td: date,
    phase: str,
    fn: Any,
    on_error: Any = None,
) -> PhaseResult:
    try:
        result = fn()
    except Exception as exc:
        handled = on_error(exc) if on_error else None
        if handled is None:
            raise
        result = handled
    assert isinstance(result, PhaseResult)
    return result


def _patch_subjects(
    monkeypatch: pytest.MonkeyPatch, tickers: list[str], seen: dict[str, Any] | None = None
) -> None:
    def fake_subjects(td: date, cap: int, universe: list[str]) -> PhaseResult:
        if seen is not None:
            seen["cap"], seen["universe"] = cap, universe
        notes = "universe empty, insight skipped" if not universe else f"{len(tickers)} subjects"
        return PhaseResult(status="SUCCESS", payload=tickers, notes=notes)

    monkeypatch.setattr(insight_mod, "insight_subjects", fake_subjects)


def test_a_quiet_hour_costs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No subject has unseen news, so the refresh must not dispatch at all."""
    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    _patch_subjects(monkeypatch, [])
    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))
    assert result.status == "SKIPPED"
    assert "1 subjects" not in result.notes


def test_an_unreadable_universe_says_so_instead_of_looking_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A universe that failed to load must not read as an ordinary quiet hour."""
    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    _patch_subjects(monkeypatch, [])

    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, []))

    assert result.status == "SKIPPED"
    assert "universe empty" in result.notes


def test_the_curated_universe_bounds_the_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The universe is what the subject query filters on, so it has to reach the task."""
    seen: dict[str, Any] = {}

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        seen["td"], seen["tickers"] = td, tickers
        return PhaseResult(status="SUCCESS", run_id="RUN1", notes="ok")

    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(insight_mod, "trigger_intraday_insight", fake_trigger)
    _patch_subjects(monkeypatch, ["BBCA", "TLKM"], seen)

    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["BBCA", "TLKM"]))

    assert result.status == "SUCCESS" and result.run_id == "RUN1"
    assert seen["universe"] == ["BBCA", "TLKM"]
    assert seen["td"] == TD and seen["tickers"] == ["BBCA", "TLKM"]


def test_the_index_can_be_an_insight_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IHSG is a curated subject like any other, and must survive the universe filter."""
    seen: dict[str, Any] = {}

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        seen["tickers"] = tickers
        return PhaseResult(status="SUCCESS", run_id="RUN2", notes="ok")

    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(insight_mod, "trigger_intraday_insight", fake_trigger)
    _patch_subjects(monkeypatch, ["IHSG"])

    insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["IHSG", "BBCA"]))

    assert seen["tickers"] == ["IHSG"]


def test_insight_trigger_failure_degrades_only_typed_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert insight_mod._degrade_on_trigger_failure(TriggerTransientError("x")) is not None
    assert insight_mod._degrade_on_trigger_failure(TriggerPermanentError("x")) is not None
    assert insight_mod._degrade_on_trigger_failure(RuntimeError("x")) is None

    def boom(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        raise TriggerTransientError("backend down")

    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(insight_mod, "trigger_intraday_insight", boom)
    _patch_subjects(monkeypatch, ["BBCA"])


    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))

    assert result.status == "DEGRADED"
    assert "insight trigger failed" in result.notes
