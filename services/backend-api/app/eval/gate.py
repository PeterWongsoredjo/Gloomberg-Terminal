"""compares candidate prompt metrics to the live baseline and blocks if they regress

if agreement drops, ticker hallucination increases, or schema validity degrades,
the prompt is rejected
"""

from __future__ import annotations

from dataclasses import dataclass

from app.eval.metrics import EvalMetrics
from app.observability.config import ObservabilitySettings, get_observability_settings


@dataclass(frozen=True)
class GateResult:
    """outcome of the gate check containing passed flag and list of block reasons"""

    passed: bool
    reasons: tuple[str, ...]


def regression_gate(
    candidate: EvalMetrics,
    live: EvalMetrics | None,
    settings: ObservabilitySettings | None = None,
) -> GateResult:
    """checks candidate metrics against current live prompt metrics and flags regressions"""
    resolved = settings or get_observability_settings()
    reasons: list[str] = []

    if candidate.non_advisory_violations > 0:
        reasons.append(f"non_advisory violations present ({candidate.non_advisory_violations})")

    if live is not None:
        if candidate.label_agreement < live.label_agreement - resolved.gate_agreement_tolerance:
            reasons.append(f"label_agreement regressed {live.label_agreement:.3f} -> {candidate.label_agreement:.3f}")
        if candidate.hallucinated_ticker_rate > live.hallucinated_ticker_rate + resolved.gate_hallucination_tolerance:
            reasons.append(
                f"hallucinated_ticker_rate regressed {live.hallucinated_ticker_rate:.3f} -> {candidate.hallucinated_ticker_rate:.3f}"
            )
        if candidate.schema_valid_rate < live.schema_valid_rate - resolved.gate_schema_tolerance:
            reasons.append(f"schema_valid_rate regressed {live.schema_valid_rate:.3f} -> {candidate.schema_valid_rate:.3f}")

    return GateResult(passed=not reasons, reasons=tuple(reasons))

