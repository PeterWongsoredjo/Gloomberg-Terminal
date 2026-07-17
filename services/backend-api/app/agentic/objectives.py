"""
Mapping of the task used in the lifecycle, we'll ladder this to the Agent
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.agentic.schemas import ArtifactType, ExtractionValue, InsightValue, SentimentValue


@dataclass(frozen=True)
class ObjectiveSpec:
    objective: str
    artifact_type: ArtifactType
    value_model: type[BaseModel]
    ladder: tuple[str, ...]
    node_name: str
    gate_attr: str


OBJECTIVES: dict[str, ObjectiveSpec] = {
    "daily_sentiment": ObjectiveSpec(
        "daily_sentiment", "SENTIMENT", SentimentValue, ("groq", "gemini"), "sentiment_analyze", "sentiment_confidence_gate"
    ),
    "intraday_sentiment": ObjectiveSpec(
        "intraday_sentiment", "SENTIMENT", SentimentValue, ("groq", "gemini"), "sentiment_analyze", "sentiment_confidence_gate"
    ),
    "deep_extraction": ObjectiveSpec(
        "deep_extraction", "EXTRACTION", ExtractionValue, ("gemini", "groq"), "deep_extract", "extraction_confidence_gate"
    ),
    "insight_synthesis": ObjectiveSpec(
        "insight_synthesis", "INSIGHT", InsightValue, ("groq", "gemini"), "synthesize_insight", "insight_confidence_gate"
    ),
    "intraday_insight": ObjectiveSpec(
        "intraday_insight", "INSIGHT", InsightValue, ("groq", "gemini"), "synthesize_insight", "insight_confidence_gate"
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
