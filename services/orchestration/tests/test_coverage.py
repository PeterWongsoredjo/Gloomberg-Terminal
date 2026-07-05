from datetime import date
from typing import Any

from orchestration.tasks.coverage import evaluate_coverage

TD = date(2026, 7, 3)


def _universe(coverage: float, missing: list[str], status: str = "SUCCESS") -> dict[str, Any]:
    return {
        "source": "idx_summary",
        "dataset": "daily_trade",
        "trade_date": TD.isoformat(),
        "status": status,
        "quality": {"coverage_ratio": coverage, "missing_tickers": missing},
    }


def _side(status: str = "SUCCESS") -> dict[str, Any]:
    return {
        "source": "corporate_actions",
        "dataset": "issued_history",
        "trade_date": TD.isoformat(),
        "status": status,
        "quality": {"coverage_ratio": 1.0, "missing_tickers": []},
    }


def test_full_coverage_promotes_success() -> None:
    result = evaluate_coverage([_universe(1.0, [])], TD, floor=0.95, hard_min=0.80)
    assert result.status == "SUCCESS"
    assert result.payload is True
    assert result.gate is not None and result.gate["promotion_ok"] is True


def test_below_floor_above_hardmin_is_partial_and_promotes() -> None:
    result = evaluate_coverage([_universe(0.90, ["AAAA", "BBBB"])], TD, floor=0.95, hard_min=0.80)
    assert result.status == "PARTIAL"
    assert result.payload is True
    assert result.gate is not None and result.gate["missing_tickers"] == ["AAAA", "BBBB"]


def test_below_hardmin_blocks_promotion() -> None:
    result = evaluate_coverage([_universe(0.70, ["AAAA"])], TD, floor=0.95, hard_min=0.80)
    assert result.status == "FAILED"
    assert result.payload is False
    assert result.gate is not None and result.gate["promotion_ok"] is False


def test_full_universe_but_degraded_side_feed_is_partial() -> None:
    manifests = [_universe(1.0, []), _side(status="PARTIAL")]
    result = evaluate_coverage(manifests, TD, floor=0.95, hard_min=0.80)
    assert result.status == "PARTIAL"
    assert result.payload is True


def test_other_dates_are_ignored() -> None:
    stale = _universe(0.10, ["ZZZZ"])
    stale["trade_date"] = "2026-01-01"
    result = evaluate_coverage([_universe(1.0, []), stale], TD, floor=0.95, hard_min=0.80)
    assert result.status == "SUCCESS"


def test_missing_universe_feed_blocks() -> None:
    result = evaluate_coverage([_side()], TD, floor=0.95, hard_min=0.80)
    assert result.status == "FAILED"
    assert result.payload is False
