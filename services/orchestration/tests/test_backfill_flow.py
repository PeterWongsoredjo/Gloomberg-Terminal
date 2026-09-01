"""The overnight drain of the unscored article backlog."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import orchestration.flow_backfill as backfill_mod
from orchestration.config import OrchestrationConfig
from orchestration.errors import TriggerTransientError
from orchestration.results import PhaseResult

TD = date(2026, 7, 27)


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


def _passthrough_run_phase(
    dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None
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


def test_the_window_runs_oldest_day_first() -> None:
    """Older articles are closest to being pruned, so they get scored first."""
    dates = backfill_mod.backfill_dates(TD, window_days=3)
    assert dates == [date(2026, 7, 24), date(2026, 7, 25), date(2026, 7, 26), TD]
    assert dates == sorted(dates)


def test_nothing_pending_dispatches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[date] = []

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        calls.append(td)
        return PhaseResult(status="SUCCESS")

    monkeypatch.setattr(backfill_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(backfill_mod, "trigger_intraday", fake_trigger)
    monkeypatch.setattr(backfill_mod, "pending_count", lambda dsn, td: 0)

    scored, status = backfill_mod._drain_date("dsn", "fr", TD, _config(tmp_path), 4)

    assert calls == [] and scored == 0 and status == "SKIPPED"


def test_a_date_is_redispatched_until_its_backlog_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One run drains a bounded batch, so a big day needs several rounds."""
    remaining = {"n": 30}

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        remaining["n"] = max(0, remaining["n"] - 10)
        return PhaseResult(status="SUCCESS")

    monkeypatch.setattr(backfill_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(backfill_mod, "trigger_intraday", fake_trigger)
    monkeypatch.setattr(backfill_mod, "pending_count", lambda dsn, td: remaining["n"])

    scored, status = backfill_mod._drain_date("dsn", "fr", TD, _config(tmp_path), 4)

    assert scored == 30 and status == "SUCCESS" and remaining["n"] == 0


def test_rounds_are_capped_so_one_night_cannot_run_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        calls.append(1)
        return PhaseResult(status="SUCCESS")

    monkeypatch.setattr(backfill_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(backfill_mod, "trigger_intraday", fake_trigger)
    counter = {"n": 500}

    def shrinking(dsn: str, td: date) -> int:
        counter["n"] -= 1
        return counter["n"]

    monkeypatch.setattr(backfill_mod, "pending_count", shrinking)

    backfill_mod._drain_date("dsn", "fr", TD, _config(tmp_path), 4)

    assert len(calls) == 4


def test_a_stuck_backlog_stops_instead_of_burning_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round that scores nothing means something is wrong, so stop paying for it."""
    calls: list[int] = []

    def fake_trigger(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        calls.append(1)
        return PhaseResult(status="SUCCESS")

    monkeypatch.setattr(backfill_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(backfill_mod, "trigger_intraday", fake_trigger)
    monkeypatch.setattr(backfill_mod, "pending_count", lambda dsn, td: 40)

    scored, _ = backfill_mod._drain_date("dsn", "fr", TD, _config(tmp_path), 4)

    assert len(calls) == 1 and scored == 0


def test_a_degraded_run_stops_the_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quota exhaustion ends the night quietly rather than failing the flow."""

    def boom(td: date, tickers: list[str], config: OrchestrationConfig) -> PhaseResult:
        raise TriggerTransientError("quota exhausted")

    monkeypatch.setattr(backfill_mod, "run_phase", _passthrough_run_phase)
    monkeypatch.setattr(backfill_mod, "trigger_intraday", boom)
    monkeypatch.setattr(backfill_mod, "pending_count", lambda dsn, td: 40)

    _, status = backfill_mod._drain_date("dsn", "fr", TD, _config(tmp_path), 4)

    assert status == "DEGRADED"
