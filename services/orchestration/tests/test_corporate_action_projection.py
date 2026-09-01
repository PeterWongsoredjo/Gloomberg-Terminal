"""Queuing corporate actions for scoring, and where that sits in the daily flow."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

import orchestration.flow as flow_mod
import orchestration.tasks.project as project_mod
from orchestration.projection import InsertOutcome
from orchestration.results import PhaseResult

TD = date(2026, 7, 15)


def _item(item_id: str, ticker: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": "idx_corporate_action",
        "lang": "en",
        "title": f"{ticker} stock split, effective 15 Jul 2026",
        "summary": f"IDX has recorded a stock split for {ticker}.",
        "url": None,
        "published_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "tickers": [ticker],
        "candidate_tickers": [],
        "item_type": "CORPORATE_ACTION",
    }


def _patch_read(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(project_mod, "read_corporate_action_items", lambda td, s: items)


def _patch_upsert(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    def upsert(dsn: str, items: list[dict[str, Any]], td: date, run_id: str | None) -> InsertOutcome:
        captured["items"] = items
        captured["trade_date"] = td
        return InsertOutcome(
            new_item_ids=[str(i["item_id"]) for i in items],
            new_tickers=sorted({t for i in items for t in i["tickers"]}),
        )

    monkeypatch.setattr(project_mod, "upsert_items", upsert)


def test_a_quiet_window_costs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most days have no corporate action, and those days must not touch Postgres."""
    _patch_read(monkeypatch, [])

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("an empty window must short-circuit before connecting")

    monkeypatch.setattr(project_mod, "upsert_items", explode)

    result = project_mod.project_corporate_actions.fn(TD)

    assert result.status == "SKIPPED"
    assert "no corporate actions" in result.notes


def test_the_window_is_queued_under_the_runs_own_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scorer claims on trade_date, so an event effective later still gets picked up."""
    captured: dict[str, Any] = {}
    _patch_read(monkeypatch, [_item("corp_action:idx_ca:1", "AADI")])
    _patch_upsert(monkeypatch, captured)

    result = project_mod.project_corporate_actions.fn(TD)

    assert result.status == "SUCCESS"
    assert captured["trade_date"] == TD
    assert captured["items"][0]["item_type"] == "CORPORATE_ACTION"
    assert captured["items"][0]["url"] is None
    assert result.payload == {"new_items": ["corp_action:idx_ca:1"], "tickers": ["AADI"]}


def test_requeuing_the_same_event_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synthetic id is content derived, so the insert-only upsert makes reruns free."""
    captured: dict[str, Any] = {}
    items = [_item("corp_action:idx_ca:1", "AADI")]
    _patch_read(monkeypatch, items)

    def upsert(dsn: str, rows: list[dict[str, Any]], td: date, run_id: str | None) -> InsertOutcome:
        captured["seen"] = [str(r["item_id"]) for r in rows]
        return InsertOutcome(new_item_ids=[], new_tickers=[])

    monkeypatch.setattr(project_mod, "upsert_items", upsert)

    result = project_mod.project_corporate_actions.fn(TD)

    assert result.status == "SUCCESS"
    assert captured["seen"] == ["corp_action:idx_ca:1"]
    assert result.payload == {"new_items": [], "tickers": []}


def test_the_phase_runs_after_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    """It reads fct_corporate_action_news out of the snapshot promote just published."""
    order: list[str] = []

    def run_phase(
        dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None
    ) -> PhaseResult:
        order.append(phase)
        return PhaseResult(status="SUCCESS", payload=True, notes="ok")

    monkeypatch.setattr(flow_mod, "run_phase", run_phase)
    monkeypatch.setattr(
        flow_mod, "_eod_insight_result", lambda *a: PhaseResult(status="SUCCESS", notes="ok")
    )
    monkeypatch.setattr(flow_mod, "get_config", lambda: _flow_config())
    monkeypatch.setattr(flow_mod, "finalize_run", lambda **kwargs: None)
    monkeypatch.setattr(flow_mod, "get_settings", _fake_settings)

    flow_mod.gloomberg_daily_flow.fn(trade_date=TD.isoformat())

    assert order.index("project_corporate_actions") > order.index("promote")
    assert order.index("promote") > order.index("dbt_build")


def test_a_projection_failure_degrades_rather_than_failing_the_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gold is already promoted by this point, so a queueing fault must not undo the day."""
    degraded = flow_mod._degrade_on_projection_failure(RuntimeError("postgres down"))

    assert degraded is not None
    assert degraded.status == "DEGRADED"
    assert "postgres down" in degraded.notes


def _flow_config() -> Any:
    from pathlib import Path

    from orchestration.config import OrchestrationConfig

    return OrchestrationConfig(
        dbt_dir=Path("."),
        calendar_seed=Path("cal.csv"),
        coverage_floor=0.95,
        coverage_hard_min=0.80,
        ingest_mode="fixture",
        backend_api_url="http://test",
        backend_api_token="",
        poll_interval_seconds=0.0,
        poll_timeout_seconds=1.0,
        trigger_timeout_seconds=1.0,
        subject_universe=["BBCA"],
    )


def _fake_settings() -> Any:
    class _S:
        postgres_dsn = "postgresql://test"

    return _S()
