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
ArtifactType = Literal["SENTIMENT", "EXTRACTION", "SUMMARY", "INSIGHT"]
Verdict = Literal["ACCEPT", "OPTIMIZE", "REJECT"]

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
    value: SentimentValue | ExtractionValue | InsightValue
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    quality_flags: list[QualityFlag] = Field(default_factory=list)
