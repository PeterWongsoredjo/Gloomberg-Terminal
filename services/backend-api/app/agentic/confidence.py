"""
If the LLM's confidence in its answer is below a certain threshold, 
the artifact is marked as low-confidence and the serving stage will render it 
as such
"""

from __future__ import annotations

from app.agentic.config import AgenticSettings
from app.agentic.objectives import spec_for
from app.core.enums import QualityFlag


def gate_for(objective: str, settings: AgenticSettings) -> float:
    return float(getattr(settings, spec_for(objective).gate_attr))


def apply_gate(
    objective: str, confidence: float, flags: list[QualityFlag], settings: AgenticSettings
) -> list[QualityFlag]:
    if confidence < gate_for(objective, settings) and QualityFlag.LLM_LOW_CONFIDENCE not in flags:
        return [*flags, QualityFlag.LLM_LOW_CONFIDENCE]
    return flags
