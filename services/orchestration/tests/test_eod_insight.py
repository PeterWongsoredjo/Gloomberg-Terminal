"""The close-of-day conclusion phase, and the rebuild that keeps it out of a one-day hole."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow as flow_mod
from orchestration.config import OrchestrationConfig
from orchestration.errors import TriggerTransientError
from orchestration.results import PhaseResult
from orchestration.tasks.dbt_build import INSIGHT_PHASES, PHASES

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


def _record_phases(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    def run_phase(
        dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None
    ) -> PhaseResult:
        order.append(phase)
        try:
            result: PhaseResult = fn()
        except Exception as exc:
            handled: PhaseResult | None = on_error(exc) if on_error else None
            if handled is None:
                raise
            return handled
        return result

    monkeypatch.setattr(flow_mod, "run_phase", run_phase)


def _patch_all(
    monkeypatch: pytest.MonkeyPatch,
    tickers: list[str],
    seen: dict[str, Any],
    trigger: Any = None,
) -> None:
    monkeypatch.setattr(
        flow_mod,
        "eod_insight_subjects",
        lambda td, cap, universe: PhaseResult(
            status="SUCCESS", payload=tickers, notes=f"{len(tickers)} subjects with news today"
        ),
    )
    monkeypatch.setattr(
        flow_mod,
        "trigger_eod_insight",
        trigger
        or (lambda td, t, c: PhaseResult(status="SUCCESS", run_id="RUN1", notes="ok")),
    )
    monkeypatch.setattr(
        flow_mod, "land_agent_artifacts", lambda td: PhaseResult(status="SUCCESS", notes="landed")
    )
    def fake_build(config: OrchestrationConfig, phases: Any = None) -> PhaseResult:
        seen["phases"] = phases
        return PhaseResult(status="SUCCESS", notes="built")

    monkeypatch.setattr(flow_mod, "dbt_build", fake_build)
    monkeypatch.setattr(flow_mod, "promote_gold", lambda: PhaseResult(status="SUCCESS", notes="up"))


def test_a_day_with_no_news_owes_no_conclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No subjects means no LLM spend and, critically, no pointless second dbt pass."""
    order: list[str] = []
    _record_phases(monkeypatch, order)
    _patch_all(monkeypatch, [], {})

    result = flow_mod._eod_insight_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))

    assert result.status == "SKIPPED"
    assert order == ["eod_insight_subjects", "trigger_eod_insight"]


def test_the_conclusion_is_landed_and_rebuilt_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Landing reads window_from = today, so tomorrow's run would never pick this up."""
    order: list[str] = []
    seen: dict[str, Any] = {}
    _record_phases(monkeypatch, order)
    _patch_all(monkeypatch, ["BBCA", "TLKM"], seen)

    result = flow_mod._eod_insight_result("dsn", "fr", TD, _config(tmp_path, ["BBCA", "TLKM"]))

    assert result.status == "SUCCESS"
    assert order == [
        "eod_insight_subjects",
        "trigger_eod_insight",
        "land_eod_insight",
        "dbt_build_insight",
        "promote_insight",
    ]


def test_the_rebuild_touches_only_the_insight_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full second build would double the daily cost for two models' worth of change."""
    seen: dict[str, Any] = {}
    _record_phases(monkeypatch, [])
    _patch_all(monkeypatch, ["BBCA"], seen)

    flow_mod._eod_insight_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))

    assert seen["phases"] is INSIGHT_PHASES
    assert seen["phases"] != PHASES
    assert [name for name, _ in INSIGHT_PHASES] == ["staging", "marts_core", "test"]


def test_a_failed_trigger_skips_the_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing new was produced, so there is nothing to land, build, or promote."""
    order: list[str] = []
    seen: dict[str, Any] = {}
    _record_phases(monkeypatch, order)

    def boom(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        raise TriggerTransientError("backend down")

    _patch_all(monkeypatch, ["BBCA"], seen, trigger=boom)

    result = flow_mod._eod_insight_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))

    assert result.status == "DEGRADED"
    assert "dbt_build_insight" not in order
    assert "phases" not in seen


def test_a_broken_rebuild_degrades_rather_than_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel serves the conclusion from Postgres, so only Gold history lags."""
    order: list[str] = []
    seen: dict[str, Any] = {}
    _record_phases(monkeypatch, order)
    _patch_all(monkeypatch, ["BBCA"], seen)

    def boom(config: OrchestrationConfig, phases: Any = None) -> PhaseResult:
        raise RuntimeError("duckdb locked")

    monkeypatch.setattr(flow_mod, "dbt_build", boom)

    result = flow_mod._eod_insight_result("dsn", "fr", TD, _config(tmp_path, ["BBCA"]))

    assert result.status == "DEGRADED"
    assert "promote_insight" in order
