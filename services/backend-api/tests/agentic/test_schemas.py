"""AG-02 schema and CT-009 envelope validation: the output-integrity contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agentic.schemas import Ct009Artifact, SentimentValue


def test_sentiment_out_of_range_score_rejected() -> None:
    """A score outside [-1, 1] fails validation (04 5.1)."""
    with pytest.raises(ValidationError):
        SentimentValue(sentiment_score=2.0, sentiment_label="BULLISH", self_confidence=0.5)


def test_sentiment_unknown_field_rejected() -> None:
    """A hallucinated field is rejected, not silently accepted (04 5.1)."""
    with pytest.raises(ValidationError):
        SentimentValue.model_validate(
            {"sentiment_score": 0.1, "sentiment_label": "NEUTRAL", "self_confidence": 0.5, "recommendation": "BUY"}
        )


def test_sentiment_has_no_advice_field() -> None:
    """The output schema exposes no prescriptive field by construction (04 1.2)."""
    assert "recommendation" not in SentimentValue.model_fields
    assert "target_price" not in SentimentValue.model_fields


def test_ct009_envelope_roundtrips_with_alias() -> None:
    """The CT-009 window uses the 'from' wire key and roundtrips cleanly."""
    artifact = Ct009Artifact.model_validate(
        {
            "artifact_id": "01ABC",
            "artifact_type": "SENTIMENT",
            "subject": {"security_id": 1, "ticker": "BBCA"},
            "window": {"from": "2026-07-03", "to": "2026-07-03"},
            "value": {
                "sentiment_score": 0.2,
                "sentiment_label": "BULLISH",
                "drivers": [],
                "evidence_item_ids": [],
                "self_confidence": 0.6,
            },
            "confidence": 0.6,
            "provenance": {
                "provider": "groq",
                "model": "llama",
                "prompt_version": "sent-v4",
                "generated_at": "2026-07-03T09:20:00Z",
            },
            "quality_flags": [],
        }
    )
    dumped = artifact.model_dump(by_alias=True, mode="json")
    assert dumped["window"]["from"] == "2026-07-03"
    assert dumped["artifact_type"] == "SENTIMENT"
