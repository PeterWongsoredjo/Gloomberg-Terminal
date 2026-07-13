"""Turns reader rows into the response view-models.

One place maps a warehouse row to what the UI renders, so the endpoints stay thin wiring. Quality
flags are coerced against the closed quality-flag vocabulary; an unrecognized flag is dropped
rather than crashing a read, and would show up as a schema drift upstream.
"""

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
    InsightSignal,
    InsightStatus,
    MatrixAxes,
    MatrixCell,
    MatrixWindow,
    NewsItem,
    PriceIntegrity,
    PriceLimit,
    SecurityRow,
    SecuritySnapshot,
    SentimentMatrix,
    LiveTapeRow,
)

_VALID_FLAGS = {f.value for f in QualityFlag}


def _flags(raw: Any) -> list[QualityFlag]:
    """Keeps only recognized quality flags; unknown values are dropped, not coerced."""
    if not raw:
        return []
    return [QualityFlag(f) for f in raw if f in _VALID_FLAGS]


def _notation(raw: Any) -> list[str]:
    return list(raw) if raw else []


def _integrity(raw: Any) -> PriceIntegrity:
    return PriceIntegrity(raw) if raw in {p.value for p in PriceIntegrity} else PriceIntegrity.CLEAN


def tape_row(row: dict[str, Any]) -> LiveTapeRow:
    """agg_live_tape row -> tape view-model; last price is the adjusted close."""
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
    """Aggregates per-security sentiment into the sector x label grid."""
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
        # DEGRADED subjects produce no sentiment row, so they are absent here, never fabricated
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
        provenance=InsightProvenance(
            provider=fct["provider"],
            model=fct["model"],
            prompt_version=fct["prompt_version"],
            trace_id=(provenance or {}).get("trace_id"),
            generated_at=fct["generated_at"],
            loop_iterations=(provenance or {}).get("consumed_iterations"),
        ),
        confidence=_as_float(fct["confidence"]) or 0.0,
        status=status,
        quality_flags=flags,
    )


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


def news_item(row: dict[str, Any]) -> NewsItem:
    return NewsItem(
        item_id=row["item_id"],
        trade_date=row["trade_date"],
        source=row["source"],
        lang=row.get("lang"),
        title=row["title"],
        summary=row.get("summary"),
        url=row["url"],
        published_at=row["published_at"],
        tickers=list(row.get("tickers") or []),
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
