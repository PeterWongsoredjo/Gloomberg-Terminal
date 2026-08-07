from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from app.core.enums import QualityFlag
from app.serving.models import (
    Candle,
    DataTelemetry,
    InsightPanel,
    InsightProvenance,
    InsightScope,
    InsightSignal,
    InsightStatus,
    MatrixAxes,
    MatrixCell,
    MatrixWindow,
    NewsItem,
    NewsItemType,
    NewsSentimentProvenance,
    NewsTickerSentiment,
    PriceIntegrity,
    PriceLimit,
    SecurityRow,
    SecuritySnapshot,
    SentimentMatrix,
    LiveTapeRow,
)

_VALID_FLAGS = {f.value for f in QualityFlag}


def _flags(raw: Any) -> list[QualityFlag]:
    """Keeps only recognized quality flags, unknown values are dropped, not coerced."""
    if not raw:
        return []
    return [QualityFlag(f) for f in raw if f in _VALID_FLAGS]


def _notation(raw: Any) -> list[str]:
    return list(raw) if raw else []


def _integrity(raw: Any) -> PriceIntegrity:
    return PriceIntegrity(raw) if raw in {p.value for p in PriceIntegrity} else PriceIntegrity.CLEAN


def _news_item_type(raw: Any) -> NewsItemType:
    """A feed row's kind, defaulting to article for rows written before the column existed."""
    return NewsItemType(raw) if raw in {t.value for t in NewsItemType} else NewsItemType.ARTICLE


def tape_row(row: dict[str, Any]) -> LiveTapeRow:
    """agg_live_tape row -> tape view-model, last price is the adjusted close."""
    return LiveTapeRow(
        security_id=row["security_id"],
        ticker=row["ticker"],
        board=row["board"],
        is_fca=bool(row["is_fca"]),
        special_notation=_notation(row.get("special_notation")),
        last_idr=row["close_adj_idr"],
        prev_idr=row.get("previous_idr"),
        change_idr=row.get("change_idr"),
        change_pct=_as_float(row.get("change_pct")),
        at_price_limit=PriceLimit(row.get("at_price_limit") or "NONE"),
        volume_shares=row.get("volume_shares"),
        value_idr=row.get("value_idr"),
        net_foreign_idr=row.get("net_foreign_idr"),
        price_series_integrity=_integrity(row.get("price_series_integrity")),
        dq_flags=_flags(row.get("dq_flags")),
    )


def security_row(row: dict[str, Any]) -> SecurityRow:
    return SecurityRow(
        security_id=row["security_id"],
        ticker=row["ticker"],
        isin=row.get("isin"),
        board=row["board"],
        sector_idxic=row.get("sector_idxic"),
        is_fca=bool(row["is_fca"]),
    )


def security_snapshot(row: dict[str, Any]) -> SecuritySnapshot:
    return SecuritySnapshot(
        security_id=row["security_id"],
        ticker=row["ticker"],
        isin=row.get("isin"),
        board=row["board"],
        sector_idxic=row.get("sector_idxic"),
        is_fca=bool(row["is_fca"]),
        special_notation=_notation(row.get("special_notation")),
        listing_date=row.get("listing_date"),
        trade_date=row["trade_date"],
        close_idr=row.get("close_idr"),
        close_adj_idr=row.get("close_adj_idr"),
        price_series_integrity=_integrity(row.get("price_series_integrity")),
        dq_flags=_flags(row.get("dq_flags")),
    )


def sentiment_matrix(rows: list[dict[str, Any]]) -> SentimentMatrix:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    versions: set[str] = set()
    dates: list[date] = []
    for r in rows:
        sector = r.get("sector_idxic") or "UNKNOWN"
        label = r.get("sentiment_label") or "NEUTRAL"
        groups[(sector, label)].append(r)
        if r.get("prompt_version"):
            versions.add(str(r["prompt_version"]))
        if r.get("trade_date"):
            dates.append(r["trade_date"])
    cells = [_matrix_cell(sector, label, members) for (sector, label), members in sorted(groups.items())]
    window = MatrixWindow(from_=min(dates), to=max(dates)) if dates else MatrixWindow(from_=date.min, to=date.min)
    return SentimentMatrix(
        as_of_window=window,
        axes=MatrixAxes(row="sector_idxic", col="sentiment_label"),
        cells=cells,
        prompt_versions=sorted(versions),
    )


def _matrix_cell(sector: str, label: str, members: list[dict[str, Any]]) -> MatrixCell:
    scores = [_as_float(m.get("sentiment_score")) or 0.0 for m in members]
    confs = [_as_float(m.get("confidence")) or 0.0 for m in members]
    low = sum(1 for m in members if "LLM_LOW_CONFIDENCE" in (m.get("dq_flags") or []))
    return MatrixCell(
        sector_idxic=sector,
        sentiment_label=label,
        security_count=len(members),
        mean_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
        mean_confidence=round(sum(confs) / len(confs), 4) if confs else 0.0,
        low_confidence_count=low,
        degraded_count=0,
    )


def insight_panel(
    fct: dict[str, Any], provenance: dict[str, Any] | None, signals: list[InsightSignal]
) -> InsightPanel:
    """fct_insight + ledger provenance + derived signals -> the Insight Panel."""
    flags = _flags(fct.get("dq_flags"))
    status = _insight_status(provenance, flags)
    return InsightPanel(
        ticker=fct["ticker"],
        headline=fct["headline"],
        narrative=fct["narrative"],
        signals=signals,
        contradictions=list(fct.get("contradictions") or []),
        watchpoints=list(fct.get("watchpoints") or []),
        scope=_insight_scope(fct["prompt_version"]),
        provenance=InsightProvenance(
            provider=fct["provider"],
            model=fct["model"],
            prompt_version=fct["prompt_version"],
            run_id=_as_str((provenance or {}).get("run_id")),
            trace_id=(provenance or {}).get("trace_id"),
            generated_at=fct["generated_at"],
            loop_iterations=(provenance or {}).get("consumed_iterations"),
        ),
        confidence=_as_float(fct["confidence"]) or 0.0,
        status=status,
        quality_flags=flags,
    )


# eod prompts conclude the day, the rest read it live
def _insight_scope(prompt_version: Any) -> InsightScope:
    return InsightScope.EOD if str(prompt_version).startswith("ins-eod") else InsightScope.INTRADAY


def _insight_status(provenance: dict[str, Any] | None, flags: list[QualityFlag]) -> InsightStatus:
    if provenance is not None and str(provenance.get("status", "")).upper() == "DEGRADED":
        return InsightStatus.DEGRADED
    if QualityFlag.LLM_LOW_CONFIDENCE in flags:
        return InsightStatus.LOW_CONFIDENCE
    return InsightStatus.OK


def candle(row: dict[str, Any]) -> Candle:
    return Candle(
        trade_date=row["trade_date"],
        open_idr=row.get("open_idr"),
        high_idr=row.get("high_idr"),
        low_idr=row.get("low_idr"),
        close_idr=row.get("close_idr"),
        close_adj_idr=row.get("close_adj_idr"),
        volume_shares=row.get("volume_shares"),
        price_series_integrity=_integrity(row.get("price_series_integrity")),
        dq_flags=_flags(row.get("dq_flags")),
    )


def news_item(
    row: dict[str, Any],
    sentiment_by_item: dict[str, dict[str, Any]] | None = None,
    breakdown_by_item: dict[str, list[dict[str, Any]]] | None = None,
    provenance_by_artifact: dict[str, dict[str, Any]] | None = None,
) -> NewsItem:
    """A headline plus its own sentiment and the per-issuer breakdown inside it."""
    item_id = str(row["item_id"])
    sentiment = (sentiment_by_item or {}).get(item_id, {})
    breakdown = (breakdown_by_item or {}).get(item_id, [])
    return NewsItem(
        item_id=item_id,
        item_type=_news_item_type(row.get("item_type")),
        trade_date=row["trade_date"],
        source=row["source"],
        lang=row.get("lang"),
        title=row["title"],
        summary=row.get("summary"),
        url=_as_str(row.get("url")),
        published_at=row["published_at"],
        tickers=list(row.get("tickers") or []),
        sentiment_score=_as_float(sentiment.get("sentiment_score")),
        sentiment_label=_as_str(sentiment.get("sentiment_label")),
        sentiment_rationale=_as_str(sentiment.get("rationale")),
        sentiment_drivers=[str(d) for d in (sentiment.get("drivers") or [])],
        ticker_sentiments=_ticker_sentiments(row, breakdown),
        sentiment_provenance=_news_sentiment_provenance(sentiment, provenance_by_artifact),
    )


def _ticker_sentiments(
    row: dict[str, Any], breakdown: list[dict[str, Any]]
) -> list[NewsTickerSentiment]:
    """Every tagged issuer, scored ones first; an unscored ticker still gets a chip."""
    scored = {
        str(entry["ticker"]): NewsTickerSentiment(
            ticker=str(entry["ticker"]),
            sentiment_score=_as_float(entry.get("sentiment_score")),
            sentiment_label=_as_str(entry.get("sentiment_label")),
            relevance=_as_str(entry.get("relevance")),
        )
        for entry in breakdown
    }
    for ticker in (str(t) for t in row.get("tickers") or []):
        scored.setdefault(
            ticker,
            NewsTickerSentiment(ticker=ticker, sentiment_score=None, sentiment_label=None, relevance=None),
        )
    return list(scored.values())


def _news_sentiment_provenance(
    sentiment: dict[str, Any], provenance_by_artifact: dict[str, dict[str, Any]] | None
) -> NewsSentimentProvenance | None:
    """Ties a headline's sentiment score to the run that produced it, run link null when off-ledger."""
    artifact_id = _as_str(sentiment.get("artifact_id"))
    if artifact_id is None:
        return None
    ledger = (provenance_by_artifact or {}).get(artifact_id, {})
    return NewsSentimentProvenance(
        artifact_id=artifact_id,
        run_id=_as_str(ledger.get("run_id")),
        trace_id=_as_str(ledger.get("trace_id")),
        provider=_as_str(sentiment.get("provider")),
        model=_as_str(sentiment.get("model")),
        prompt_version=_as_str(sentiment.get("prompt_version")),
        confidence=_as_float(sentiment.get("confidence")),
        generated_at=sentiment.get("generated_at"),
        evidence_item_ids=[str(i) for i in (sentiment.get("evidence_item_ids") or [])],
    )


def data_telemetry(row: dict[str, Any]) -> DataTelemetry:
    """obs_telemetry_rollup record -> the telemetry panel view-model."""
    return DataTelemetry(
        trade_date=row["trade_date"],
        session_state=row["session_state"],
        coverage_ratio=_as_float(row.get("coverage_ratio")),
        missing_ticker_count=row.get("missing_ticker_count"),
        quarantine_row_count=row.get("quarantine_row_count"),
        dbt_tests_passed=row.get("dbt_tests_passed"),
        dbt_tests_failed=row.get("dbt_tests_failed"),
        gold_promoted_at=row.get("gold_promoted_at"),
        gold_promotion_ok=row.get("gold_promotion_ok"),
        slo_breaches=_as_json_list(row.get("slo_breaches")),
        active_alerts=_as_json_list(row.get("active_alerts")),
        llm_runs=row.get("llm_runs") or 0,
        llm_runs_degraded=row.get("llm_runs_degraded") or 0,
        total_tokens=row.get("total_tokens") or 0,
        notional_cost=_as_float(row.get("notional_cost")) or 0.0,
        quota_pct_groq=_as_float(row.get("quota_pct_groq")) or 0.0,
        quota_pct_gemini=_as_float(row.get("quota_pct_gemini")) or 0.0,
        breaker_state_groq=row.get("breaker_state_groq"),
        breaker_state_gemini=row.get("breaker_state_gemini"),
        low_confidence_artifact_count=row.get("low_confidence_artifact_count") or 0,
        live_prompt_versions=_as_json_dict(row.get("live_prompt_versions")),
    )


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_json_list(value: Any) -> list[dict[str, object]]:
    import json

    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _as_json_dict(value: Any) -> dict[str, str]:
    import json

    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    return {}
