"""The backfill has to land what it scores, or past days never reach Gold."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow_backfill as backfill_mod
from orchestration.config import OrchestrationConfig
from orchestration.results import PhaseResult

TD = date(2026, 9, 12)


def _config(tmp_path: Path) -> OrchestrationConfig:
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
    )


def _record_phases(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Runs each phase for real but remembers the order they were asked for."""

    def _run(dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None) -> PhaseResult:
        seen.append(phase)
        try:
            return fn()  # type: ignore[no-any-return]
        except Exception as exc:
            handled = on_error(exc) if on_error else None
            if handled is None:
                raise
            return handled  # type: ignore[no-any-return]

    monkeypatch.setattr(backfill_mod, "run_phase", _run)


def _stub(monkeypatch: pytest.MonkeyPatch, name: str, result: PhaseResult) -> None:
    monkeypatch.setattr(backfill_mod, name, lambda *a, **k: result)


def test_a_landed_day_triggers_a_rebuild_and_a_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _record_phases(monkeypatch, seen)
    _stub(monkeypatch, "reconcile_news_items", PhaseResult(status="SUCCESS", notes="3 days"))
    _stub(monkeypatch, "reconcile_agent_artifacts", PhaseResult(status="SKIPPED", notes="none"))
    _stub(monkeypatch, "dbt_build", PhaseResult(status="SUCCESS", notes="built"))
    _stub(monkeypatch, "promote_gold", PhaseResult(status="SUCCESS", notes="promoted"))

    statuses = backfill_mod._recover_missed_days("dsn", "run", TD, _config(tmp_path))

    assert seen == ["reconcile_news_items", "reconcile_agent_artifacts", "dbt_build", "promote"]
    assert statuses == ["SUCCESS", "SUCCESS", "SUCCESS"]


def test_nothing_missing_skips_the_rebuild_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full build is expensive, so an empty reconcile must not trigger one."""
    seen: list[str] = []
    _record_phases(monkeypatch, seen)
    _stub(monkeypatch, "reconcile_news_items", PhaseResult(status="SKIPPED", notes="none"))
    _stub(monkeypatch, "reconcile_agent_artifacts", PhaseResult(status="SKIPPED", notes="none"))

    statuses = backfill_mod._recover_missed_days("dsn", "run", TD, _config(tmp_path))

    assert seen == ["reconcile_news_items", "reconcile_agent_artifacts"]
    assert statuses == []


def test_a_broken_reconcile_degrades_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _record_phases(monkeypatch, seen)

    def _boom(*a: Any, **k: Any) -> PhaseResult:
        raise RuntimeError("minio unreachable")

    monkeypatch.setattr(backfill_mod, "reconcile_news_items", _boom)
    _stub(monkeypatch, "reconcile_agent_artifacts", PhaseResult(status="SKIPPED", notes="none"))

    statuses = backfill_mod._recover_missed_days("dsn", "run", TD, _config(tmp_path))

    assert statuses == ["DEGRADED"]
    assert "dbt_build" not in seen


def test_recovery_status_reaches_the_flow_rollup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded recovery must not be hidden by a clean scoring pass."""
    monkeypatch.setattr(backfill_mod, "backfill_dates", lambda td, window_days=14: [])
    monkeypatch.setattr(backfill_mod, "finalize_run", lambda **k: None)
    monkeypatch.setattr(backfill_mod, "get_config", lambda: _config(tmp_path))
    monkeypatch.setattr(backfill_mod, "get_settings", lambda: type("S", (), {"postgres_dsn": "dsn"})())
    monkeypatch.setattr(
        backfill_mod, "_recover_missed_days", lambda dsn, run, td, config: ["DEGRADED"]
    )

    assert backfill_mod.backfill_sentiment_flow.fn(trade_date=TD.isoformat()) == "DEGRADED"
