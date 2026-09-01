"""
Mapping of the task used in the lifecycle, we'll ladder this to the Agent
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.agentic.schemas import (
    ArticleSentimentBatch,
    ArticleSentimentValue,
    ArtifactType,
    CashDividendValue,
    ExtractionValue,
    InsightValue,
    SentimentValue,
)


@dataclass(frozen=True)
class ObjectiveSpec:
    objective: str
    artifact_type: ArtifactType
    value_model: type[BaseModel]
    ladder: tuple[str, ...]
    node_name: str
    gate_attr: str
    request_model: type[BaseModel] | None = None

    @property
    def response_model(self) -> type[BaseModel]:
        """What the provider is asked to return."""
        return self.request_model or self.value_model


OBJECTIVES: dict[str, ObjectiveSpec] = {
    "daily_sentiment": ObjectiveSpec(
        "daily_sentiment", "SENTIMENT", SentimentValue, ("groq", "gemini"), "sentiment_analyze", "sentiment_confidence_gate"
    ),
    "article_sentiment": ObjectiveSpec(
        "article_sentiment", "ARTICLE_SENTIMENT", ArticleSentimentValue, ("gemini", "groq"),
        "sentiment_analyze", "sentiment_confidence_gate", request_model=ArticleSentimentBatch,
    ),
    "deep_extraction": ObjectiveSpec(
        "deep_extraction", "EXTRACTION", ExtractionValue, ("gemini", "groq"), "deep_extract", "extraction_confidence_gate"
    ),
    "dividend_extraction": ObjectiveSpec(
        "dividend_extraction", "CASH_DIVIDEND", CashDividendValue, ("gemini", "groq"),
        "deep_extract", "extraction_confidence_gate",
    ),
    "insight_synthesis": ObjectiveSpec(
        "insight_synthesis", "INSIGHT", InsightValue, ("gemini", "groq"), "synthesize_insight", "insight_confidence_gate"
    ),
    "intraday_insight": ObjectiveSpec(
        "intraday_insight", "INSIGHT", InsightValue, ("gemini", "groq"), "synthesize_insight", "insight_confidence_gate"
    ),
}

# first objective declared for a type stays its canonical spec
_BY_TYPE: dict[str, ObjectiveSpec] = {}
for _spec in OBJECTIVES.values():
    _BY_TYPE.setdefault(_spec.artifact_type, _spec)


def spec_for(objective: str) -> ObjectiveSpec:
    return OBJECTIVES[objective]


def spec_for_type(artifact_type: str) -> ObjectiveSpec:
    return _BY_TYPE[artifact_type]
