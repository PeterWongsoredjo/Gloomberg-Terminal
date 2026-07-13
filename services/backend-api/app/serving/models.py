from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.core.enums import QualityFlag, SessionPhase


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceLimit(StrEnum):
    NONE = "NONE"
    ARA = "ARA"
    ARB = "ARB"


class PriceIntegrity(StrEnum):
    CLEAN = "CLEAN"
    ADJUSTMENT_PENDING = "ADJUSTMENT_PENDING"


class InsightStatus(StrEnum):
    OK = "OK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"


# --- market/state ---


class IndexLevel(_Model):
    index_code: str
    level: float
    change: float | None


class MarketState(_Model):
    trade_date: date
    session_phase: SessionPhase
    is_trading_day: bool
    close_time_utc: datetime
    ihsg: IndexLevel | None


# --- live tape ---


class LiveTapeRow(_Model):
    security_id: int
    ticker: str
    board: str
    is_fca: bool
    special_notation: list[str]
    last_idr: int
    prev_idr: int | None
    change_idr: int | None
    change_pct: float | None
    at_price_limit: PriceLimit
    volume_shares: int | None
    value_idr: int | None
    net_foreign_idr: int | None
    price_series_integrity: PriceIntegrity
    dq_flags: list[QualityFlag]


class LiveTapePage(_Model):
    trade_date: date
    rows: list[LiveTapeRow]


# --- securities universe ---


class SecurityRow(_Model):
    security_id: int
    ticker: str
    isin: str | None
    board: str
    sector_idxic: str | None
    is_fca: bool


class SecurityPage(_Model):
    rows: list[SecurityRow]
    next_cursor: str | None


class SecuritySnapshot(_Model):
    security_id: int
    ticker: str
    isin: str | None
    board: str
    sector_idxic: str | None
    is_fca: bool
    special_notation: list[str]
    listing_date: date | None
    trade_date: date
    close_idr: int | None
    close_adj_idr: int | None
    price_series_integrity: PriceIntegrity
    dq_flags: list[QualityFlag]


# --- sentiment matrix ---


class MatrixWindow(_Model):
    from_: date
    to: date


class MatrixAxes(_Model):
    row: str
    col: str


class MatrixCell(_Model):
    sector_idxic: str
    sentiment_label: str
    security_count: int
    mean_score: float
    mean_confidence: float
    low_confidence_count: int
    degraded_count: int


class SentimentMatrix(_Model):
    as_of_window: MatrixWindow
    axes: MatrixAxes
    cells: list[MatrixCell]
    prompt_versions: list[str]


# --- insight panel ---


class InsightSignal(_Model):
    type: str
    value: str
    confidence: float | None


class InsightProvenance(_Model):
    provider: str
    model: str
    prompt_version: str
    trace_id: str | None
    generated_at: datetime
    loop_iterations: int | None


class InsightPanel(_Model):
    ticker: str
    headline: str
    narrative: str
    signals: list[InsightSignal]
    contradictions: list[str]
    provenance: InsightProvenance
    confidence: float
    status: InsightStatus
    quality_flags: list[QualityFlag]


# --- data telemetry ---


class DataTelemetry(_Model):
    trade_date: date
    session_state: str
    coverage_ratio: float | None
    missing_ticker_count: int | None
    quarantine_row_count: int | None
    dbt_tests_passed: int | None
    dbt_tests_failed: int | None
    gold_promoted_at: datetime | None
    gold_promotion_ok: bool | None
    slo_breaches: list[dict[str, object]]
    active_alerts: list[dict[str, object]]
    llm_runs: int
    llm_runs_degraded: int
    total_tokens: int
    notional_cost: float
    quota_pct_groq: float
    quota_pct_gemini: float
    breaker_state_groq: str | None
    breaker_state_gemini: str | None
    low_confidence_artifact_count: int
    live_prompt_versions: dict[str, str]


# --- price series / candles (additive) ---


class Candle(_Model):
    trade_date: date
    open_idr: int | None
    high_idr: int | None
    low_idr: int | None
    close_idr: int | None
    close_adj_idr: int | None
    volume_shares: int | None
    price_series_integrity: PriceIntegrity
    dq_flags: list[QualityFlag]


class PriceSeries(_Model):
    ticker: str
    bars: list[Candle]


# --- news feed (additive) ---


class NewsItem(_Model):
    item_id: str
    trade_date: date
    source: str
    lang: str | None
    title: str
    summary: str | None
    url: str
    published_at: datetime
    tickers: list[str]


class NewsFeedPage(_Model):
    rows: list[NewsItem]
    next_cursor: str | None


# --- run reasoning trace (additive) ---


class TraceStep(_Model):
    node: str
    status: str
    detail: str | None


class RunReasoningTrace(_Model):
    run_id: str
    objective: str
    trade_date: date
    status: str
    loop_iterations: int
    trace_id: str | None
    steps: list[TraceStep]
