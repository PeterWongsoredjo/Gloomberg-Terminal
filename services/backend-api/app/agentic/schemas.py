"""AG-02 structured output schemas and the CT-009 provenance envelope.

Every model call must return JSON conforming to one of these closed schemas; free text is
never accepted as a fact. The value models forbid unknown fields so a hallucinated key fails
validation instead of slipping through.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import QualityFlag

SentimentLabel = Literal["BEARISH", "NEUTRAL", "BULLISH"]
ArtifactType = Literal["SENTIMENT", "EXTRACTION", "SUMMARY", "INSIGHT"]
Verdict = Literal["ACCEPT", "OPTIMIZE", "REJECT"]

# CT-007 corporate-action enum plus the extraction escape hatch
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
    """Rejects any field not declared here, so hallucinated keys fail validation."""

    model_config = ConfigDict(extra="forbid")


class SentimentValue(_Strict):
    """The value block of a SENTIMENT artifact."""

    sentiment_score: float = Field(ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel
    drivers: list[str] = Field(default_factory=list, max_length=5)
    evidence_item_ids: list[str] = Field(default_factory=list)
    self_confidence: float = Field(ge=0.0, le=1.0)


class Event(_Strict):
    """One extracted market event inside an EXTRACTION artifact."""

    event_type: EventType
    security_id: int | None = None
    fields: dict[str, str | int] = Field(default_factory=dict)
    source_span: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionValue(_Strict):
    """The value block of an EXTRACTION artifact."""

    events: list[Event] = Field(default_factory=list)
    unresolved_entities: list[str] = Field(default_factory=list)


class InsightSignal(_Strict):
    """One supporting signal cited by an INSIGHT narrative."""

    type: Literal["SENTIMENT", "FLOW", "PRICE"]
    ref_artifact_id: str | None = None
    value: str | None = None


class InsightValue(_Strict):
    """The value block of an INSIGHT artifact; no advisory field exists by design."""

    headline: str
    narrative: str
    signals: list[InsightSignal] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class EvaluatorChecks(_Strict):
    """The AG-04 hard gates plus the calibrated confidence."""

    schema_valid: bool
    grounded: bool
    entities_resolved: bool
    non_advisory: bool
    context_consistent: bool
    confidence_calibrated: float = Field(ge=0.0, le=1.0)


class EvaluatorVerdict(_Strict):
    """The AG-04 verdict that drives the optimize-or-accept edge."""

    checks: EvaluatorChecks
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    evaluator_provider: str


class Subject(_Strict):
    """The instrument an artifact is about."""

    security_id: int | None = None
    ticker: str | None = None


class Window(_Strict):
    """The WIB trade-date window an artifact covers."""

    from_: date = Field(alias="from")
    to: date

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TokenUsage(_Strict):
    """Prompt and completion token counts for one artifact's inference."""

    prompt: int = 0
    completion: int = 0


class Provenance(_Strict):
    """Everything needed to reproduce an artifact against its exact inputs."""

    provider: str
    model: str
    prompt_version: str
    trace_id: str | None = None
    input_source_refs: list[str] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    generated_at: datetime
    loop_iterations: int = 0


class Ct009Artifact(_Strict):
    """The CT-009 envelope: the only shape a model-derived value reaches the warehouse in."""

    schema_version: str = "1.0.0"
    artifact_id: str
    artifact_type: ArtifactType
    subject: Subject
    window: Window
    value: SentimentValue | ExtractionValue | InsightValue
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    quality_flags: list[QualityFlag] = Field(default_factory=list)
