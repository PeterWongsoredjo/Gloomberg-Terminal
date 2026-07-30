"""
Structured output schemas for the LLM, dictating the exact JSON shape for each 
artifact type. Wrapped in an envelope to log out each prompt
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import QualityFlag

SentimentLabel = Literal["BEARISH", "NEUTRAL", "BULLISH"]
ArtifactType = Literal["SENTIMENT", "ARTICLE_SENTIMENT", "EXTRACTION", "SUMMARY", "INSIGHT"]
Verdict = Literal["ACCEPT", "OPTIMIZE", "REJECT"]

TickerRelevance = Literal["PRIMARY", "SECONDARY", "INCIDENTAL"]

# corporate-action enum plus the extraction escape hatch
EventType = Literal[
    "CASH_DIVIDEND",
    "STOCK_DIVIDEND",
    "STOCK_SPLIT",
    "REVERSE_SPLIT",
    "RIGHTS_ISSUE",
    "BONUS_SHARES",
    "WARRANT_EXERCISE",
    "TENDER_OFFER",
    "DELISTING",
    "RELISTING",
    "SUSPENSION",
    "UNSUSPENSION",
    "OTHER",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SentimentValue(_Strict):
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel
    drivers: list[str] = Field(default_factory=list, max_length=5)
    evidence_item_ids: list[str] = Field(default_factory=list)
    self_confidence: float = Field(ge=0.0, le=1.0)


class TickerSentiment(_Strict):
    """How one article reads for one issuer it names."""

    ticker: str = Field(pattern=r"^[A-Z]{4}$")
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel
    relevance: TickerRelevance


class ArticleVerdict(_Strict):
    """One article's own read, plus the per-issuer breakdown inside it."""

    item_id: str
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel
    # absent on older verdicts, never invented
    rationale: str | None = Field(default=None, max_length=400)
    drivers: list[str] = Field(default_factory=list, max_length=3)
    ticker_sentiments: list[TickerSentiment] = Field(default_factory=list, max_length=6)
    self_confidence: float = Field(ge=0.0, le=1.0)


class ArticleSentimentBatch(_Strict):
    """What one batched request returns: a verdict per article it was handed."""

    verdicts: list[ArticleVerdict] = Field(default_factory=list, max_length=16)


class ArticleSentimentValue(ArticleVerdict):
    """One article's verdict as a stored artifact, with the tickers we refused to trust."""

    dropped_tickers: list[str] = Field(default_factory=list)


class Event(_Strict):
    event_type: EventType
    security_id: int | None = None
    fields: dict[str, str | int] = Field(default_factory=dict)
    source_span: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionValue(_Strict):
    events: list[Event] = Field(default_factory=list)
    unresolved_entities: list[str] = Field(default_factory=list)


class InsightSignal(_Strict):
    type: Literal["SENTIMENT", "FLOW", "PRICE"]
    ref_artifact_id: str | None = None
    value: str | None = None


class InsightValue(_Strict):
    headline: str
    narrative: str
    signals: list[InsightSignal] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    # what the close of day leaves open, empty in session
    watchpoints: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)


class EvaluatorChecks(_Strict):
    schema_valid: bool
    grounded: bool
    entities_resolved: bool
    non_advisory: bool
    context_consistent: bool
    confidence_calibrated: float = Field(ge=0.0, le=1.0)


class EvaluatorVerdict(_Strict):
    checks: EvaluatorChecks
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    evaluator_provider: str


class Subject(_Strict):
    security_id: int | None = None
    ticker: str | None = None


class Window(_Strict):
    from_: date = Field(alias="from")
    to: date

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TokenUsage(_Strict):
    prompt: int = 0
    completion: int = 0


class Provenance(_Strict):
    provider: str
    model: str
    prompt_version: str
    trace_id: str | None = None
    input_source_refs: list[str] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    generated_at: datetime
    loop_iterations: int = 0


class Ct009Artifact(_Strict):
    schema_version: str = "1.0.0"
    artifact_id: str
    artifact_type: ArtifactType
    subject: Subject
    window: Window
    value: SentimentValue | ArticleSentimentValue | ExtractionValue | InsightValue
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    quality_flags: list[QualityFlag] = Field(default_factory=list)
