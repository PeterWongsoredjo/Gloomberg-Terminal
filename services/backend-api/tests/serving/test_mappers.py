"""Row-to-view-model mapping: field mapping, flag coercion, and matrix aggregation."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from app.core.enums import QualityFlag
from app.serving import mappers
from app.serving.models import InsightScope, InsightStatus, NewsItemType, PriceLimit


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
    low = mappers.insight_panel(
        fct, {"run_id": "01JZX", "trace_id": "t", "consumed_iterations": 2, "status": "SUCCEEDED"}, []
    )
    assert low.status is InsightStatus.LOW_CONFIDENCE
    assert low.provenance.run_id == "01JZX"
    assert low.provenance.trace_id == "t"
    assert low.provenance.loop_iterations == 2

    degraded = mappers.insight_panel(fct, {"trace_id": None, "consumed_iterations": 1, "status": "DEGRADED"}, [])
    assert degraded.status is InsightStatus.DEGRADED
    assert degraded.provenance.run_id is None


def _insight_fct(prompt_version: str, watchpoints: list[str] | None = None) -> dict[str, object]:
    return {
        "ticker": "TLKM", "headline": "h", "narrative": "n", "contradictions": [],
        "watchpoints": watchpoints if watchpoints is not None else [],
        "confidence": 0.8, "provider": "groq", "model": "m", "prompt_version": prompt_version,
        "generated_at": datetime.now(UTC), "dq_flags": [],
    }


def test_insight_scope_is_read_off_the_prompt_version() -> None:
    """Scope is derived, not stored, so the version prefix is the whole contract."""
    assert mappers.insight_panel(_insight_fct("ins-eod-v1"), None, []).scope is InsightScope.EOD
    for version in ("ins-intraday-v3", "ins-v1", "rollup-v1", ""):
        assert mappers.insight_panel(_insight_fct(version), None, []).scope is InsightScope.INTRADAY


def test_insight_watchpoints_survive_the_mapper() -> None:
    carried = ["RUPS scheduled 30 Jul", "Foreign flow negative 4 sessions"]
    panel = mappers.insight_panel(_insight_fct("ins-eod-v1", carried), None, [])
    assert panel.watchpoints == carried
    assert mappers.insight_panel(_insight_fct("ins-eod-v1"), None, []).watchpoints == []


def _news_row(item_id: str = "n1", tickers: list[str] | None = None) -> dict[str, object]:
    return {
        "item_id": item_id, "trade_date": date(2026, 7, 3), "source": "KONTAN", "lang": "id",
        "title": "t", "summary": None, "url": "https://x", "published_at": datetime.now(UTC),
        "tickers": tickers if tickers is not None else ["BBCA", "BMRI"],
    }


def test_news_item_scores_the_article_not_its_first_ticker() -> None:
    """The row's colour must come from this headline, not from whichever ticker sorted first."""
    sentiment = {"n1": {"sentiment_score": 0.4, "sentiment_label": "BULLISH"}}
    item = mappers.news_item(_news_row(), sentiment)
    assert item.sentiment_score == 0.4
    assert item.sentiment_label == "BULLISH"


def test_an_unscored_article_stays_null_never_zero() -> None:
    bare = mappers.news_item(_news_row(), {})
    assert bare.sentiment_score is None
    assert bare.sentiment_label is None
    assert bare.sentiment_provenance is None


def test_each_ticker_in_a_multi_ticker_article_keeps_its_own_read() -> None:
    """One headline can be good for one issuer and bad for another."""
    breakdown = {
        "n1": [
            {"ticker": "BBCA", "sentiment_score": 0.5, "sentiment_label": "BULLISH", "relevance": "PRIMARY"},
            {"ticker": "BMRI", "sentiment_score": -0.3, "sentiment_label": "BEARISH", "relevance": "SECONDARY"},
        ]
    }
    item = mappers.news_item(_news_row(), {"n1": {"sentiment_score": 0.2}}, breakdown)
    by_ticker = {t.ticker: t for t in item.ticker_sentiments}
    assert by_ticker["BBCA"].sentiment_label == "BULLISH"
    assert by_ticker["BMRI"].sentiment_label == "BEARISH"
    assert by_ticker["BMRI"].relevance == "SECONDARY"


def test_a_tagged_but_unscored_ticker_still_gets_a_chip() -> None:
    """The ticker stays clickable even before its verdict lands."""
    item = mappers.news_item(_news_row(tickers=["BBCA", "GOTO"]), {}, {})
    assert [t.ticker for t in item.ticker_sentiments] == ["BBCA", "GOTO"]
    assert all(t.sentiment_score is None for t in item.ticker_sentiments)


def test_a_macro_article_with_no_tickers_still_carries_a_score() -> None:
    """Two thirds of the live feed looks like this and it must not render blank."""
    sentiment = {"n1": {"sentiment_score": -0.25, "sentiment_label": "BEARISH"}}
    item = mappers.news_item(_news_row(tickers=[]), sentiment)
    assert item.sentiment_score == -0.25
    assert item.ticker_sentiments == []


def test_news_item_carries_the_models_reasoning_and_evidence() -> None:
    """These two are what the article pane renders; dropping them was the original bug."""
    sentiment = {
        "n1": {
            "sentiment_score": -0.4, "sentiment_label": "BEARISH",
            "rationale": "Provisioning guidance rose while loan growth held flat.",
            "drivers": ["provisi naik", "NIM tertekan"],
        }
    }
    item = mappers.news_item(_news_row(), sentiment)
    assert item.sentiment_rationale == "Provisioning guidance rose while loan growth held flat."
    assert item.sentiment_drivers == ["provisi naik", "NIM tertekan"]


def test_a_pre_v2_article_reports_no_reasoning_rather_than_an_empty_one() -> None:
    """Absent has to stay absent so the pane can fall back to the evidence phrases."""
    item = mappers.news_item(_news_row(), {"n1": {"sentiment_score": 0.1, "drivers": ["cuan"]}})
    assert item.sentiment_rationale is None
    assert item.sentiment_drivers == ["cuan"]

    unscored = mappers.news_item(_news_row(), {})
    assert unscored.sentiment_rationale is None and unscored.sentiment_drivers == []


def test_news_item_carries_sentiment_provenance() -> None:
    row = _news_row(tickers=["BBRI"])
    sentiment = {
        "n1": {
            "sentiment_score": -0.38, "sentiment_label": "BEARISH", "confidence": 0.7,
            "artifact_id": "art-1", "provider": "gemini", "model": "gemini-3.1-flash-lite",
            "prompt_version": "art-sent-v1", "generated_at": datetime.now(UTC),
            "evidence_item_ids": ["n1"],
        }
    }
    provenance = {"art-1": {"run_id": "run-9", "trace_id": "tr-9"}}
    item = mappers.news_item(row, sentiment, {}, provenance)
    prov = item.sentiment_provenance
    assert prov is not None
    assert prov.artifact_id == "art-1"
    assert prov.run_id == "run-9"
    assert prov.trace_id == "tr-9"
    assert prov.provider == "gemini"

    # fail-open: no ledger row means score/provider still set, run link null
    off_ledger = mappers.news_item(row, sentiment, {}, {})
    assert off_ledger.sentiment_provenance is not None
    assert off_ledger.sentiment_provenance.run_id is None
    assert off_ledger.sentiment_provenance.trace_id is None
    assert off_ledger.sentiment_provenance.provider == "gemini"
    assert off_ledger.sentiment_score == -0.38


def test_a_corporate_action_row_carries_its_type_and_a_null_url() -> None:
    """IDX publishes no per-event page, so the url is absent rather than invented."""
    row = _news_row("corp_action:idx_ca:999001", ["AADI"])
    row["item_type"] = "CORPORATE_ACTION"
    row["url"] = None

    item = mappers.news_item(row, {"corp_action:idx_ca:999001": {"sentiment_label": "BULLISH"}})

    assert item.item_type == NewsItemType.CORPORATE_ACTION
    assert item.url is None
    assert item.sentiment_label == "BULLISH"


def test_a_row_without_the_type_column_reads_as_an_article() -> None:
    """A snapshot published before the column existed must not 500 the feed."""
    assert mappers.news_item(_news_row()).item_type == NewsItemType.ARTICLE


def test_an_unknown_type_is_not_trusted_as_a_new_kind() -> None:
    """Vocabulary drift falls back to article rather than inventing a rendering branch."""
    row = _news_row()
    row["item_type"] = "PRESS_RELEASE"
    assert mappers.news_item(row).item_type == NewsItemType.ARTICLE


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
