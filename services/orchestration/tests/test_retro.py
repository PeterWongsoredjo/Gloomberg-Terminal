"""The manual backfill that may judge a past day against a later company list."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pipeline.quality.coverage import CoverageResult

from orchestration import flow_retro
from orchestration.results import PhaseResult
from orchestration.tasks import coverage as gate
from orchestration.tasks import retro

OLD = date(2026, 9, 17)
LATER = date(2026, 10, 11)
SEED = Path("no-such-calendar.csv")  # no overrides, so weekday rules apply


def _result(ratio: float = 1.0, missing: list[str] | None = None, retro_days: int = 24) -> CoverageResult:
    return CoverageResult(
        expected_universe=100,
        observed_universe=round(ratio * 100),
        missing_tickers=missing or [],
        coverage_ratio=ratio,
        as_of=LATER,
        stale_days=0,
        authority_key="company_profile/profiles/ingest_date=2026-10-11/part-x-0000.json.zst",
        retro_days=retro_days,
    )


# ------------------------------------------------------------------ the gate


def test_a_retro_measurement_earns_the_retro_flag() -> None:
    assert gate.quality_flags(_result(), 0.95) == ["RETRO_AUTHORITY"]


def test_retro_and_a_real_gap_are_both_flagged() -> None:
    """Rewound membership does not excuse a day that is genuinely short."""
    assert gate.quality_flags(_result(0.90, ["AAAA"]), 0.95) == ["RETRO_AUTHORITY", "COVERAGE_GAP"]


def test_a_live_measurement_never_earns_the_retro_flag() -> None:
    assert "RETRO_AUTHORITY" not in gate.quality_flags(_result(retro_days=0), 0.95)


def test_a_complete_retro_day_promotes_and_says_why() -> None:
    """Retro is a caveat on the evidence, not a degrade of the prices."""
    verdict = gate.evaluate_coverage([], {"daily_trade": _result()}, OLD, 0.95, 0.80)

    assert verdict.status == "SUCCESS"
    assert verdict.payload is True
    assert verdict.gate is not None and verdict.gate["universe_retro_days"] == 24
    assert gate.RETRO_AUTHORITY in verdict.notes
    assert "24d after 2026-09-17" in verdict.notes


def test_a_short_retro_day_still_blocks() -> None:
    """The hard minimum holds in backfill mode exactly as it does every day."""
    verdict = gate.evaluate_coverage([], {"daily_trade": _result(0.70, ["AAAA"])}, OLD, 0.95, 0.80)
    assert verdict.payload is False


def test_a_live_day_reports_zero_retro_days() -> None:
    verdict = gate.evaluate_coverage([], {"daily_trade": _result(retro_days=0)}, OLD, 0.95, 0.80)
    assert verdict.gate is not None and verdict.gate["universe_retro_days"] == 0
    assert gate.RETRO_AUTHORITY not in verdict.notes


def test_the_shared_gate_defaults_to_no_retro(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daily flow and the resweep call without the switch, so they stay strict."""
    seen: list[bool] = []
    monkeypatch.setattr(gate, "get_settings", lambda: None)
    monkeypatch.setattr(gate, "client", lambda settings: None)

    def measure(*a: Any, retro: bool = False, **kw: Any) -> CoverageResult:
        seen.append(retro)
        return _result(retro_days=0)

    monkeypatch.setattr(gate, "measure", measure)
    monkeypatch.setattr(gate, "revise_manifest", lambda *a, **kw: None)

    gate.run_coverage_gate([], OLD, 0.95, 0.80, 500, SEED)
    gate.run_coverage_gate([], OLD, 0.95, 0.80, 500, SEED, retro=True)

    assert seen == [False, True]


def test_the_retro_flag_is_stamped_on_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gold reads the flag off the manifest, so it has to land there."""
    stamped: dict[str, Any] = {}
    monkeypatch.setattr(gate, "get_settings", lambda: None)
    monkeypatch.setattr(gate, "client", lambda settings: None)
    monkeypatch.setattr(gate, "measure", lambda *a, **kw: _result())
    monkeypatch.setattr(
        gate, "revise_manifest",
        lambda minio, spec, td, result, status, flags: stamped.update(status=status, flags=flags),
    )

    gate.measure_and_revise(OLD, 0.95, 0.80, 500, SEED, retro=True)

    assert stamped == {"status": "SUCCESS", "flags": ["RETRO_AUTHORITY"]}


# ------------------------------------------------------------------ the window


def test_the_window_is_trading_days_oldest_first() -> None:
    """Weekends drop out, and the order is the order Gold should fill in."""
    days = retro.retro_window(date(2026, 9, 17), date(2026, 9, 22), LATER, SEED)
    assert days == [date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22)]


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(ValueError, match="backwards"):
        retro.retro_window(date(2026, 9, 22), date(2026, 9, 17), LATER, SEED)


def test_a_window_reaching_today_is_refused() -> None:
    """Today has a same-day capture coming, so retro authority must never touch it."""
    with pytest.raises(ValueError, match="before today"):
        retro.retro_window(date(2026, 10, 5), LATER, LATER, SEED)


def test_the_retro_gate_task_turns_the_switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(retro, "get_settings", lambda: None)
    monkeypatch.setattr(retro, "client", lambda settings: None)
    monkeypatch.setattr(retro, "universe_manifests", lambda minio, td: [])

    def fake_gate(manifests: Any, td: date, *a: Any, retro: bool = False) -> PhaseResult:
        seen.update(td=td, retro=retro)
        return PhaseResult(status="SUCCESS", payload=True)

    monkeypatch.setattr(retro, "run_coverage_gate", fake_gate)
    retro.retro_coverage_gate.fn(OLD)

    assert seen == {"td": OLD, "retro": True}


# ------------------------------------------------------------------ the flow


def _passthrough(dsn: str, flow_run_id: str, td: date, phase: str, fn: Any, on_error: Any = None) -> PhaseResult:
    result = fn()
    assert isinstance(result, PhaseResult)
    return result


def _flow(monkeypatch: pytest.MonkeyPatch, passing: set[date]) -> dict[str, Any]:
    """Wires the flow to fakes and records what it rebuilt and how it finished."""
    record: dict[str, Any] = {"rebuilds": [], "gated": []}
    monkeypatch.setattr(flow_retro, "get_settings", lambda: type("S", (), {"postgres_dsn": "dsn"})())
    monkeypatch.setattr(flow_retro, "get_config", lambda: type("C", (), {"calendar_seed": SEED})())
    monkeypatch.setattr(flow_retro, "wib_today", lambda: LATER)
    monkeypatch.setattr(flow_retro, "run_phase", _passthrough)

    def gate_day(td: date) -> PhaseResult:
        record["gated"].append(td)
        ok = td in passing
        return PhaseResult(status="SUCCESS" if ok else "FAILED", payload=ok)

    def rebuild(dsn: str, run: str, td: date, config: Any, prefix: str) -> PhaseResult:
        record["rebuilds"].append((td, prefix))
        return PhaseResult(status="SUCCESS")

    monkeypatch.setattr(flow_retro, "retro_coverage_gate", gate_day)
    monkeypatch.setattr(flow_retro, "rebuild_gold", rebuild)
    monkeypatch.setattr(flow_retro, "finalize_run", lambda **kw: record.update(final=kw))
    return record


def test_the_flow_rebuilds_gold_once_for_the_whole_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seventeen days must cost one build and one promote, not seventeen."""
    record = _flow(monkeypatch, passing={date(2026, 9, 17), date(2026, 9, 18)})

    status = flow_retro.idx_retro_backfill_flow.fn("2026-09-17", "2026-09-18")

    assert record["gated"] == [date(2026, 9, 17), date(2026, 9, 18)]
    assert record["rebuilds"] == [(date(2026, 9, 18), "retro")]
    assert status == "SUCCESS"
    assert "2 of 2 days passed" in record["final"]["notes"]


def test_a_blocked_day_shows_in_the_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The days that passed still ship, and the run admits the one that did not."""
    record = _flow(monkeypatch, passing={date(2026, 9, 17)})

    status = flow_retro.idx_retro_backfill_flow.fn("2026-09-17", "2026-09-18")

    assert record["rebuilds"] == [(date(2026, 9, 18), "retro")]
    assert status == "FAILED"
    assert "1 of 2 days passed" in record["final"]["notes"]


def test_nothing_passing_never_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _flow(monkeypatch, passing=set())

    flow_retro.idx_retro_backfill_flow.fn("2026-09-17", "2026-09-18")

    assert record["rebuilds"] == []


def test_a_window_into_today_fails_before_judging_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal happens up front and is still recorded as a finished run."""
    record = _flow(monkeypatch, passing={LATER})

    with pytest.raises(ValueError, match="before today"):
        flow_retro.idx_retro_backfill_flow.fn("2026-10-09", LATER.isoformat())

    assert record["gated"] == []
    assert record["final"]["overall_status"] == "FAILED"
