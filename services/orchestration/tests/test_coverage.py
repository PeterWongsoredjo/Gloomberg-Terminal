"""The OR-06 promotion gate, driven by measured coverage rather than a declared ratio."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pipeline.quality.coverage import CoverageResult
from pipeline.quality.universe import UniverseUnavailable

from orchestration.tasks import coverage as gate
from orchestration.tasks.coverage import blocked, coverage_gate, evaluate_coverage

TD = date(2026, 7, 3)
SEED = Path("no-such-calendar.csv")  # no overrides, so weekday rules apply


def _measured(
    ratio: float, missing: list[str], expected: int = 100, stale: int = 0
) -> dict[str, CoverageResult]:
    return {
        "daily_trade": CoverageResult(
            expected_universe=expected,
            observed_universe=round(ratio * expected),
            missing_tickers=missing,
            coverage_ratio=ratio,
            as_of=TD,
            stale_days=stale,
            authority_key="company_profile/profiles/ingest_date=2026-07-03/part-x-0000.json.zst",
        )
    }


def _universe_manifest(
    status: str = "PARTIAL", evaluated: bool = False, object_count: int = 1
) -> dict[str, Any]:
    quality = (
        {"coverage_ratio": 1.0, "expected_universe": 100, "observed_universe": 100}
        if evaluated
        else {"coverage_ratio": None, "expected_universe": None, "observed_universe": None}
    )
    return {
        "source": "idx_summary",
        "dataset": "daily_trade",
        "trade_date": TD.isoformat(),
        "status": status,
        "object_count": object_count,
        "quality": {**quality, "missing_tickers": []},
    }


def _side(status: str = "SUCCESS") -> dict[str, Any]:
    return {
        "source": "corporate_actions",
        "dataset": "issued_history",
        "trade_date": TD.isoformat(),
        "status": status,
        "quality": {
            "coverage_ratio": None,
            "expected_universe": None,
            "observed_universe": None,
            "missing_tickers": [],
        },
    }


def test_full_coverage_promotes_success() -> None:
    result = evaluate_coverage([], _measured(1.0, []), TD, floor=0.95, hard_min=0.80)
    assert result.status == "SUCCESS"
    assert result.payload is True
    assert result.gate is not None and result.gate["promotion_ok"] is True


def test_below_floor_above_hardmin_is_partial_and_promotes() -> None:
    result = evaluate_coverage(
        [], _measured(0.90, ["AAAA", "BBBB"]), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "PARTIAL"
    assert result.payload is True
    assert result.gate is not None and result.gate["missing_tickers"] == ["AAAA", "BBBB"]


def test_below_hardmin_blocks_promotion() -> None:
    result = evaluate_coverage([], _measured(0.70, ["AAAA"]), TD, floor=0.95, hard_min=0.80)
    assert result.status == "FAILED"
    assert result.payload is False
    assert result.gate is not None and result.gate["promotion_ok"] is False


def test_full_universe_but_degraded_side_feed_is_partial() -> None:
    manifests = [_universe_manifest(), _side(status="PARTIAL")]
    result = evaluate_coverage(manifests, _measured(1.0, []), TD, floor=0.95, hard_min=0.80)
    assert result.status == "PARTIAL"
    assert result.payload is True


def test_a_universe_feed_awaiting_the_gate_is_not_a_degrade() -> None:
    """Its own PARTIAL means unmeasured, and the gate is what measures it."""
    result = evaluate_coverage(
        [_universe_manifest()], _measured(1.0, []), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "SUCCESS"


def test_other_dates_are_ignored() -> None:
    stale = _side(status="FAILED")
    stale["trade_date"] = "2026-01-01"
    result = evaluate_coverage(
        [_universe_manifest(), stale], _measured(1.0, []), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "SUCCESS"


def test_missing_universe_feed_blocks() -> None:
    result = evaluate_coverage([_side()], {}, TD, floor=0.95, hard_min=0.80)
    assert result.status == "FAILED"
    assert result.payload is False


def test_the_gate_reports_the_real_universe_numbers() -> None:
    """The panel and the operator need the counts, not just the ratio."""
    result = evaluate_coverage(
        [], _measured(0.75, ["ERAA"], expected=4), TD, floor=0.95, hard_min=0.50
    )
    assert result.gate is not None
    assert result.gate["expected_universe"] == 4
    assert result.gate["observed_universe"] == 3
    assert result.gate["missing_tickers"] == ["ERAA"]
    assert result.gate["universe_as_of"] == "2026-07-03"


def test_a_stale_universe_blocks_even_at_full_coverage() -> None:
    """Yesterday's membership cannot prove today is whole, whatever the ratio says."""
    result = evaluate_coverage(
        [], _measured(1.0, [], stale=3), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "FAILED"
    assert result.payload is False
    assert result.gate is not None
    assert result.gate["universe_stale_days"] == 3
    assert result.gate["promotion_ok"] is False
    assert result.gate["reason"] == gate.STALE_AUTHORITY
    assert gate.STALE_AUTHORITY in result.notes


def test_a_stale_universe_is_still_measured_for_telemetry() -> None:
    """Blocking is not a reason to stop counting; the operator needs the numbers."""
    result = evaluate_coverage(
        [], _measured(0.90, ["AAAA"], expected=10, stale=3), TD, floor=0.95, hard_min=0.80
    )
    assert result.gate is not None
    assert result.gate["coverage_ratio"] == 0.90
    assert result.gate["expected_universe"] == 10
    assert result.gate["missing_tickers"] == ["AAAA"]


def test_same_day_authority_still_promotes() -> None:
    """The policy costs availability on a stale day, and nothing on a good one."""
    result = evaluate_coverage([], _measured(1.0, [], stale=0), TD, floor=0.95, hard_min=0.80)
    assert result.status == "SUCCESS"
    assert result.payload is True
    assert result.gate is not None and "reason" not in result.gate


def test_an_unavailable_authority_blocks_and_invents_nothing() -> None:
    """Fail closed: no expected universe means no promotion, never a guessed ratio."""
    result = blocked("expected universe unavailable: no company_profile snapshot")
    assert result.status == "FAILED"
    assert result.payload is False
    assert result.gate is not None
    assert result.gate["coverage_ratio"] is None
    assert result.gate["promotion_ok"] is False
    assert "no company_profile snapshot" in result.notes


def test_the_gate_task_fails_closed_on_an_unavailable_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*a: Any, **kw: Any) -> dict[str, CoverageResult]:
        raise UniverseUnavailable("no company_profile snapshot on or before 2026-07-03")

    monkeypatch.setattr(gate, "measure_and_revise", unavailable)
    result = coverage_gate.fn([_universe_manifest()], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "FAILED"
    assert result.payload is False
    assert result.gate is not None and result.gate["coverage_ratio"] is None


def test_the_gate_task_measures_and_stamps_before_deciding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decision reads what the gate itself measured, not what landing claimed."""
    seen: dict[str, Any] = {}

    def measured(
        td: date, floor: float, hard_min: float, min_securities: int, seed: Path
    ) -> Any:
        seen.update(td=td, floor=floor, hard_min=hard_min, min_securities=min_securities)
        return _measured(0.60, ["GONE", "MSNG"], expected=5)

    monkeypatch.setattr(gate, "measure_and_revise", measured)
    result = coverage_gate.fn([_universe_manifest()], TD, 0.95, 0.80, 500, SEED)

    assert seen == {"td": TD, "floor": 0.95, "hard_min": 0.80, "min_securities": 500}
    assert result.status == "FAILED"
    assert result.gate is not None and result.gate["missing_tickers"] == ["GONE", "MSNG"]


def test_a_lying_manifest_cannot_promote_an_incomplete_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: a manifest claiming 1.0 no longer decides anything."""
    liar = _universe_manifest(status="SUCCESS", evaluated=True)
    monkeypatch.setattr(
        gate, "measure_and_revise", lambda *a, **kw: _measured(0.70, ["AAAA"], expected=10)
    )
    result = coverage_gate.fn([liar], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "FAILED"
    assert result.payload is False


def test_a_gap_above_the_floor_still_degrades() -> None:
    """47 of 959 clears a 0.95 floor, and a day short 47 securities is not a clean day."""
    result = evaluate_coverage(
        [], _measured(0.9509, ["UNVR"], expected=957), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "PARTIAL"
    assert result.payload is True
    assert result.gate is not None and result.gate["missing_tickers"] == ["UNVR"]


def test_a_failed_universe_fetch_blocks_before_anything_is_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older bytes still in Bronze must not let a given-up fetch promote as a clean day."""
    monkeypatch.setattr(
        gate, "measure_and_revise", lambda *a, **kw: pytest.fail("must not measure or stamp")
    )
    gave_up = _universe_manifest(status="FAILED", object_count=0)
    result = coverage_gate.fn([gave_up], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "FAILED"
    assert result.payload is False
    assert "never landed" in result.notes


def test_a_day_this_gate_already_failed_can_still_be_re_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate stamps FAILED itself, so that stamp must not lock the day out forever."""
    already_failed = _universe_manifest(status="FAILED", evaluated=True, object_count=1)
    monkeypatch.setattr(gate, "measure_and_revise", lambda *a, **kw: _measured(1.0, []))

    result = coverage_gate.fn([already_failed], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "SUCCESS"
    assert result.payload is True


def test_a_universe_feed_awaiting_the_gate_is_not_yet_a_verdict() -> None:
    """The flow's ingest rollup must not read 'unmeasured' as 'degraded'."""
    assert gate.awaiting_coverage(_universe_manifest()) is True
    assert gate.awaiting_coverage(_universe_manifest(status="SUCCESS", evaluated=True)) is False
    assert gate.awaiting_coverage(_side(status="PARTIAL")) is False


def test_the_decision_table_puts_staleness_before_the_ratio() -> None:
    fresh = {"degraded": False, "stale": False}
    assert gate.coverage_status(1.00, 0.95, 0.80, **fresh) == "SUCCESS"
    assert gate.coverage_status(1.00, 0.95, 0.80, degraded=True, stale=False) == "PARTIAL"
    assert gate.coverage_status(0.90, 0.95, 0.80, **fresh) == "PARTIAL"
    assert gate.coverage_status(0.70, 0.95, 0.80, **fresh) == "FAILED"
    # stale wins outright, however good the ratio is
    assert gate.coverage_status(1.00, 0.95, 0.80, degraded=False, stale=True) == "FAILED"


def _seed(tmp_path: Path, rows: str = "") -> Path:
    seed = tmp_path / "ref_market_calendar.csv"
    seed.write_text(
        "trade_date,is_trading_day,session_template,holiday_reason\n" + rows, encoding="utf-8"
    )
    return seed


def test_the_authority_window_is_the_date_and_the_session_before_it(tmp_path: Path) -> None:
    """Monday accepts Friday, because Saturday and Sunday are not sessions."""
    monday, friday = date(2026, 7, 6), date(2026, 7, 3)
    assert gate.allowed_authority_dates(monday, _seed(tmp_path)) == frozenset({monday, friday})


def test_the_authority_window_skips_a_holiday(tmp_path: Path) -> None:
    """2026-08-17 is Independence Day, so Tuesday falls back past it to the Friday."""
    seed = _seed(tmp_path, "2026-08-17,false,NORMAL,Indonesian Independence Day\n")
    tuesday = date(2026, 8, 18)
    assert gate.allowed_authority_dates(tuesday, seed) == frozenset(
        {tuesday, date(2026, 8, 14)}
    )


def test_the_authority_window_never_reaches_two_sessions_back(tmp_path: Path) -> None:
    """Thursday is two sessions before Monday, so it is not in the window at all."""
    window = gate.allowed_authority_dates(date(2026, 7, 6), _seed(tmp_path))
    assert date(2026, 7, 2) not in window
    assert len(window) == 2


def test_measure_and_revise_stamps_failed_when_the_authority_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest and the PhaseResult have to tell the operator the same thing."""
    stamped: dict[str, str] = {}
    monkeypatch.setattr(gate, "get_settings", lambda: None)
    monkeypatch.setattr(gate, "client", lambda settings: None)
    monkeypatch.setattr(
        gate, "measure", lambda *a, **kw: _measured(1.0, [], stale=3)["daily_trade"]
    )
    monkeypatch.setattr(
        gate,
        "revise_manifest",
        lambda minio, spec, td, result, status, flags: stamped.update(
            {spec.dataset: status, "flags": flags}
        ),
    )

    results = gate.measure_and_revise(TD, 0.95, 0.80, 500, SEED)
    verdict = evaluate_coverage([], results, TD, 0.95, 0.80)

    assert stamped == {"daily_trade": "FAILED", "flags": ["STALE"]}
    assert verdict.status == "FAILED"
    assert verdict.payload is False


def test_measure_and_revise_stamps_success_on_same_day_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamped: dict[str, str] = {}
    monkeypatch.setattr(gate, "get_settings", lambda: None)
    monkeypatch.setattr(gate, "client", lambda settings: None)
    monkeypatch.setattr(
        gate, "measure", lambda *a, **kw: _measured(1.0, [], stale=0)["daily_trade"]
    )
    monkeypatch.setattr(
        gate,
        "revise_manifest",
        lambda minio, spec, td, result, status, flags: stamped.update(
            {spec.dataset: status, "flags": flags}
        ),
    )

    results = gate.measure_and_revise(TD, 0.95, 0.80, 500, SEED)

    assert stamped == {"daily_trade": "SUCCESS", "flags": []}
    assert evaluate_coverage([], results, TD, 0.95, 0.80).status == "SUCCESS"


def test_a_stale_blocked_gate_recovers_once_same_day_authority_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resweep lands today's profile, and the very next gate run may promote."""
    monkeypatch.setattr(gate, "measure_and_revise", lambda *a, **kw: _measured(1.0, [], stale=3))
    blocked_run = coverage_gate.fn([_universe_manifest()], TD, 0.95, 0.80, 500, SEED)

    monkeypatch.setattr(gate, "measure_and_revise", lambda *a, **kw: _measured(1.0, [], stale=0))
    recovered = coverage_gate.fn([_universe_manifest()], TD, 0.95, 0.80, 500, SEED)

    assert blocked_run.status == "FAILED" and blocked_run.payload is False
    assert recovered.status == "SUCCESS" and recovered.payload is True


def test_the_ct008_flags_follow_the_contract_not_the_status() -> None:
    """STALE is about freshness and COVERAGE_GAP is about the ratio; they are not the same."""
    stale_full = _measured(1.0, [], stale=3)["daily_trade"]
    fresh_gap = _measured(0.90, ["AAAA"])["daily_trade"]
    both = _measured(0.90, ["AAAA"], stale=3)["daily_trade"]
    clean = _measured(1.0, [])["daily_trade"]

    assert gate.quality_flags(stale_full, 0.95) == ["STALE"]
    assert gate.quality_flags(fresh_gap, 0.95) == ["COVERAGE_GAP"]
    assert gate.quality_flags(both, 0.95) == ["STALE", "COVERAGE_GAP"]
    assert gate.quality_flags(clean, 0.95) == []


def test_a_ratio_exactly_on_the_floor_is_not_a_gap() -> None:
    assert gate.quality_flags(_measured(0.95, [])["daily_trade"], 0.95) == []


def test_a_fetch_failure_manifest_stops_leftover_bytes_being_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid object from an earlier attempt must not be promoted after the retry died."""
    gave_up = _universe_manifest(status="FAILED", object_count=0)
    monkeypatch.setattr(
        gate, "measure_and_revise", lambda *a, **kw: pytest.fail("must not touch leftover bytes")
    )

    result = gate.run_coverage_gate([gave_up], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "FAILED"
    assert result.payload is False
    assert "never landed" in result.notes


def test_a_gate_stamped_failure_with_real_bytes_stays_re_measurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate writes FAILED itself; that stamp must never lock a genuine recovery out."""
    stamped = _universe_manifest(status="FAILED", evaluated=True, object_count=1)
    monkeypatch.setattr(gate, "measure_and_revise", lambda *a, **kw: _measured(1.0, []))

    result = gate.run_coverage_gate([stamped], TD, 0.95, 0.80, 500, SEED)

    assert result.status == "SUCCESS"
    assert result.payload is True


def test_the_gate_task_and_the_shared_function_are_the_same_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resweep calls the plain function; both paths must decide identically."""
    monkeypatch.setattr(gate, "measure_and_revise", lambda *a, **kw: _measured(0.70, ["AAAA"]))
    args = ([_universe_manifest()], TD, 0.95, 0.80, 500, SEED)

    via_task = coverage_gate.fn(*args)
    via_function = gate.run_coverage_gate(*args)

    assert (via_task.status, via_task.payload) == (via_function.status, via_function.payload)
    assert via_task.payload is False


# ------------------------- transport completeness reaches the gate, not just the manifest


def _landed_universe(record_count: int, declared: int, object_count: int = 1) -> dict[str, Any]:
    """A daily_trade landing carrying the numbers the gate has to read for itself."""
    return {
        "source": "idx_summary",
        "dataset": "daily_trade",
        "trade_date": TD.isoformat(),
        "status": "PARTIAL",
        "object_count": object_count,
        "record_count": record_count,
        "upstream": {"declared_record_total": declared},
        "quality": {
            "expected_universe": 957,
            "observed_universe": 957,
            "missing_tickers": [],
            "coverage_ratio": 1.0,
            "dq_flags": [],
        },
    }


def _complete_universe() -> dict[str, CoverageResult]:
    return {
        "daily_trade": CoverageResult(
            expected_universe=957,
            observed_universe=957,
            missing_tickers=[],
            coverage_ratio=1.0,
            as_of=TD,
            stale_days=0,
            authority_key="company_profile/profiles/ingest_date=2026-07-03/part-x-0000.json.zst",
        )
    }


def test_a_transport_truncated_universe_feed_degrades_the_gate() -> None:
    """957 of 959 rows arrived and they were every expected security: promotable, not clean."""
    result = evaluate_coverage(
        [_landed_universe(957, 959)], _complete_universe(), TD, floor=0.95, hard_min=0.80
    )

    assert result.status == "PARTIAL"
    assert result.payload is True
    assert result.gate is not None
    assert result.gate["promotion_ok"] is True
    assert result.gate["coverage_ratio"] == 1.0
    assert result.gate["expected_universe"] == 957
    assert result.gate["observed_universe"] == 957
    assert result.gate["missing_tickers"] == []
    # transport truncation is not a coverage gap, and must not be dressed as one
    assert gate.quality_flags(_complete_universe()["daily_trade"], 0.95) == []
    assert "COVERAGE_GAP" not in result.notes
    assert "upstream sent fewer rows than it declared" in result.notes


def test_a_clean_universe_landing_is_success() -> None:
    """The control: nothing missing anywhere, so nothing to degrade."""
    result = evaluate_coverage(
        [_landed_universe(959, 959)], _complete_universe(), TD, floor=0.95, hard_min=0.80
    )

    assert result.status == "SUCCESS"
    assert result.payload is True
    assert result.gate is not None and result.gate["promotion_ok"] is True


def test_the_gate_reads_the_numbers_not_the_manifest_status() -> None:
    """A landing whose own numbers agree is clean, whatever its stale status field claims."""
    stale_status = {**_landed_universe(959, 959), "status": "PARTIAL"}
    assert (
        evaluate_coverage([stale_status], _complete_universe(), TD, 0.95, 0.80).status == "SUCCESS"
    )


def test_transport_truncation_cannot_rescue_a_failed_coverage() -> None:
    """Worst wins in both directions: a below-hard-min day still blocks."""
    result = evaluate_coverage(
        [_landed_universe(957, 959)], _measured(0.70, ["AAAA"]), TD, floor=0.95, hard_min=0.80
    )
    assert result.status == "FAILED"
    assert result.payload is False


def test_the_gate_and_the_revised_manifest_reach_the_same_status() -> None:
    """Both sides run the same worst-wins rule over the same two verdicts."""
    from pipeline.quality.revise import revised

    landed = _landed_universe(957, 959)
    measured = _complete_universe()["daily_trade"]

    from_gate = evaluate_coverage([landed], _complete_universe(), TD, 0.95, 0.80)
    from_manifest = revised(landed, measured, "SUCCESS")

    assert from_gate.status == from_manifest["status"] == "PARTIAL"
