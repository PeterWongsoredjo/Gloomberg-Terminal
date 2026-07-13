"""Row-to-view-model mapping: field mapping, flag coercion, and matrix aggregation."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from app.core.enums import QualityFlag
from app.serving import mappers
from app.serving.models import InsightStatus, PriceLimit


def _tape_raw(**over: Any) -> dict[str, Any]:
    base = {
        "security_id": 1,
        "ticker": "BBCA",
        "board": "MAIN",
        "is_fca": False,
        "special_notation": [],
        "close_adj_idr": 9875,
        "previous_idr": 9950,
        "change_idr": -75,
        "change_pct": -0.0075,
        "at_price_limit": "NONE",
        "volume_shares": 41_200_000,
        "value_idr": 407_150_000_000,
        "net_foreign_idr": -18_400_000_000,
        "price_series_integrity": "CLEAN",
        "dq_flags": [],
    }
    base.update(over)
    return base


def test_tape_row_maps_all_fields() -> None:
    row = mappers.tape_row(_tape_raw(at_price_limit="ARA", dq_flags=["STALE", "BOGUS"]))
    assert row.last_idr == 9875
    assert row.prev_idr == 9950
    assert row.at_price_limit is PriceLimit.ARA
    assert row.change_pct == -0.0075
    # unknown flag dropped, known flag kept
    assert row.dq_flags == [QualityFlag.STALE]


def test_sentiment_matrix_aggregates_by_sector_and_label() -> None:
    rows = [
        {"sector_idxic": "IDXFINANCE", "sentiment_label": "BEARISH", "sentiment_score": -0.4,
         "confidence": 0.7, "prompt_version": "sent-v4", "trade_date": date(2026, 7, 3), "dq_flags": []},
        {"sector_idxic": "IDXFINANCE", "sentiment_label": "BEARISH", "sentiment_score": -0.2,
         "confidence": 0.5, "prompt_version": "sent-v4", "trade_date": date(2026, 7, 3),
         "dq_flags": ["LLM_LOW_CONFIDENCE"]},
    ]
    matrix = mappers.sentiment_matrix(rows)
    assert len(matrix.cells) == 1
    cell = matrix.cells[0]
    assert cell.security_count == 2
    assert cell.mean_score == -0.3
    assert cell.low_confidence_count == 1
    assert matrix.prompt_versions == ["sent-v4"]


def test_insight_status_reflects_degraded_and_low_confidence() -> None:
    fct = {
        "ticker": "TLKM", "headline": "h", "narrative": "n", "contradictions": [],
        "confidence": 0.4, "provider": "groq", "model": "m", "prompt_version": "ins-v1",
        "generated_at": datetime.now(UTC), "dq_flags": ["LLM_LOW_CONFIDENCE"],
    }
    low = mappers.insight_panel(fct, {"trace_id": "t", "consumed_iterations": 2, "status": "SUCCEEDED"}, [])
    assert low.status is InsightStatus.LOW_CONFIDENCE
    assert low.provenance.trace_id == "t"
    assert low.provenance.loop_iterations == 2

    degraded = mappers.insight_panel(fct, {"trace_id": None, "consumed_iterations": 1, "status": "DEGRADED"}, [])
    assert degraded.status is InsightStatus.DEGRADED


def test_data_telemetry_parses_json_columns() -> None:
    row = {
        "trade_date": date(2026, 7, 3), "session_state": "CLOSED", "coverage_ratio": 0.97,
        "missing_ticker_count": 2, "quarantine_row_count": 0, "dbt_tests_passed": 69,
        "dbt_tests_failed": 0, "gold_promoted_at": None, "gold_promotion_ok": True,
        "slo_breaches": json.dumps([{"slo_id": "coverage_floor"}]), "active_alerts": "[]",
        "llm_runs": 12, "llm_runs_degraded": 1, "total_tokens": 8573, "notional_cost": 0.0,
        "quota_pct_groq": 0.1, "quota_pct_gemini": 0.0, "breaker_state_groq": "CLOSED",
        "breaker_state_gemini": None, "low_confidence_artifact_count": 1,
        "live_prompt_versions": json.dumps({"daily_sentiment": "sent-v4"}),
    }
    telemetry = mappers.data_telemetry(row)
    assert telemetry.slo_breaches == [{"slo_id": "coverage_floor"}]
    assert telemetry.live_prompt_versions == {"daily_sentiment": "sent-v4"}
    assert telemetry.total_tokens == 8573
