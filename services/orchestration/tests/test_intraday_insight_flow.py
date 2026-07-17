from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow as flow_mod
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
        objective="daily_sentiment",
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


def test_insight_trigger_skips_on_empty_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, []))
    assert result.status == "SKIPPED"
    assert "universe empty" in result.notes


def test_insight_trigger_dispatches_full_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        seen["td"], seen["tickers"] = td, tickers
        return PhaseResult(status="SUCCESS", run_id="RUN1", notes="ok")

    monkeypatch.setattr(insight_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(insight_mod, "trigger_intraday_insight", fake_trigger)
    config = _config(tmp_path, ["BBCA", "TLKM"])
    result = insight_mod._trigger_result("dsn", "fr", TD, config)
    assert result.status == "SUCCESS" and result.run_id == "RUN1"
    assert seen == {"td": TD, "tickers": ["BBCA", "TLKM"]}


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
    result = insight_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))
    assert result.status == "DEGRADED"
    assert "insight trigger failed" in result.notes


def test_daily_trigger_skips_on_empty_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_mod, "run_phase", _passthrough_run_phase)
    result = flow_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, []))
    assert result.status == "SKIPPED"
    assert "no curated equities" in result.notes


def test_daily_trigger_skips_on_index_only_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_mod, "run_phase", _passthrough_run_phase)
    result = flow_mod._trigger_result("dsn", "fr", TD, _config(tmp_path, ["IHSG"]))
    assert result.status == "SKIPPED"
    assert "no curated equities" in result.notes


def test_daily_trigger_fires_over_equities_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_trigger(td: date, config: OrchestrationConfig) -> PhaseResult:
        seen["universe"] = config.subject_universe
        return PhaseResult(status="SUCCESS", run_id="RUN2", notes="ok")

    monkeypatch.setattr(flow_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(flow_mod, "trigger_agentic", fake_trigger)
    config = replace(_config(tmp_path, ["IHSG", "BBCA", "TLKM"]))
    result = flow_mod._trigger_result("dsn", "fr", TD, config)
    assert result.status == "SUCCESS" and result.run_id == "RUN2"
    assert seen["universe"] == ["BBCA", "TLKM"]
