"""Confidence gating: below the objective's gate, an artifact is marked low-confidence.

Self-reported confidence is a weak signal, so the evaluator's calibrated confidence wins when
present. Gating never drops an artifact; it flags it so serving renders it as low-confidence
rather than authoritative (CT-008).
"""

from __future__ import annotations

from app.agentic.config import AgenticSettings
from app.agentic.objectives import spec_for
from app.core.enums import QualityFlag


def gate_for(objective: str, settings: AgenticSettings) -> float:
    """The configured confidence threshold for an objective."""
    return float(getattr(settings, spec_for(objective).gate_attr))


def apply_gate(
    objective: str, confidence: float, flags: list[QualityFlag], settings: AgenticSettings
) -> list[QualityFlag]:
    """Adds LLM_LOW_CONFIDENCE when the confidence is below the gate, once."""
    if confidence < gate_for(objective, settings) and QualityFlag.LLM_LOW_CONFIDENCE not in flags:
        return [*flags, QualityFlag.LLM_LOW_CONFIDENCE]
    return flags
