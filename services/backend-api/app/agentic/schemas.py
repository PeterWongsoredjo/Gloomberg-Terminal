"""
Structured output schemas for the LLM, dictating the exact JSON shape for each 
artifact type. Wrapped in an envelope to log out each prompt
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agentic.amount import amount_sen
from app.core.enums import QualityFlag

SentimentLabel = Literal["BEARISH", "NEUTRAL", "BULLISH"]
ArtifactType = Literal[
    "SENTIMENT", "ARTICLE_SENTIMENT", "EXTRACTION", "SUMMARY", "INSIGHT", "CASH_DIVIDEND"
]
Verdict = Literal["ACCEPT", "OPTIMIZE", "REJECT"]

TickerRelevance = Literal["PRIMARY", "SECONDARY", "INCIDENTAL"]

DividendKind = Literal["INTERIM", "FINAL", "SPECIAL", "UNSPECIFIED"]
DividendOutcome = Literal["EXTRACTED", "NO_DIVIDEND_STATED"]
ExtractionOutcome = Literal["EXTRACTED", "NOTHING_EXTRACTABLE"]

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
    outcome: ExtractionOutcome
    events: list[Event] = Field(default_factory=list)
    unresolved_entities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_matches_events(self) -> ExtractionValue:
        """An empty extraction must say so, never pass as a silent success."""
        if self.outcome == "EXTRACTED" and not self.events:
            raise ValueError("EXTRACTED needs at least one event")
        if self.outcome == "NOTHING_EXTRACTABLE" and self.events:
            raise ValueError("NOTHING_EXTRACTABLE cannot carry events")
        return self


class CashDividendEvent(_Strict):
    """One declared cash dividend line, as the filing states it."""

    ticker: str = Field(pattern=r"^[A-Z]{4}$")
    dividend_kind: DividendKind
    currency: Literal["IDR", "USD"]
    amount_text: str = Field(min_length=1, max_length=40)
    amount_per_share_sen: int | None = Field(default=None, ge=0)
    ex_date: date | None = None
    recording_date: date | None = None
    payment_date: date | None = None
    source_span: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _derive_amount(self) -> CashDividendEvent:
        """Our parser owns the number, so the model can never supply one."""
        self.amount_per_share_sen = amount_sen(self.amount_text, self.currency)
        return self


class CashDividendValue(_Strict):
    """What one filing yielded: the dividends it declares, or a stated absence."""

    filing_id: str = Field(min_length=1)
    outcome: DividendOutcome
    events: list[CashDividendEvent] = Field(default_factory=list, max_length=6)
    reason: str = Field(default="", max_length=200)
    unresolved: list[str] = Field(default_factory=list)
    filing_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _outcome_matches_events(self) -> CashDividendValue:
        """A filing that declares nothing has to say why, and claim no amounts."""
        if self.outcome == "EXTRACTED" and not self.events:
            raise ValueError("EXTRACTED needs at least one event")
        if self.outcome == "NO_DIVIDEND_STATED" and (self.events or not self.reason.strip()):
            raise ValueError("NO_DIVIDEND_STATED needs a reason and no events")
        return self


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
    value: SentimentValue | ArticleSentimentValue | ExtractionValue | InsightValue | CashDividendValue
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    quality_flags: list[QualityFlag] = Field(default_factory=list)
