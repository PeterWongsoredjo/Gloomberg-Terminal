"""
runs the evaluation and quality gating flow to promote a candidate prompt to live
stages the new draft, calculates metrics, runs the regression gate, and updates states
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from app.eval import lifecycle
from app.eval.gate import regression_gate
from app.eval.harness import Predictor, run_offline_eval
from app.eval.metrics import EvalMetrics
from app.observability.config import ObservabilitySettings


@dataclass(frozen=True)
class PromotionOutcome:
    """summary of a promotion run containing the final state and reasons if rejected"""

    objective: str
    version: str
    state: str
    reasons: tuple[str, ...]
    mlflow_run_id: str


async def promote_prompt(
    pool: asyncpg.Pool,
    *,
    objective: str,
    candidate_version: str,
    candidate_predict: Predictor,
    model: str,
    provider: str,
    content_sha256: str,
    live_metrics: EvalMetrics | None = None,
    settings: ObservabilitySettings | None = None,
    dataset_version: str = "golden-2026Q2",
) -> PromotionOutcome:
    """runs evaluation on a candidate and promotes it if it clears the quality baseline"""
    await lifecycle.setup(pool)
    await lifecycle.register_draft(pool, objective=objective, version=candidate_version, content_sha256=content_sha256)

    result = run_offline_eval(
        prompt_version=candidate_version,
        provider=provider,
        model=model,
        predict=candidate_predict,
        dataset_version=dataset_version,
        settings=settings,
    )
    await lifecycle.mark_evaluated(pool, objective=objective, version=candidate_version, mlflow_run_id=result.mlflow_run_id)

    gate = regression_gate(result.metrics, live_metrics, settings)
    if gate.passed:
        await lifecycle.promote_to_live(pool, objective=objective, version=candidate_version)
        state = "LIVE"
    else:
        await lifecycle.reject(pool, objective=objective, version=candidate_version, reasons=list(gate.reasons))
        state = "REJECTED"

    return PromotionOutcome(objective, candidate_version, state, gate.reasons, result.mlflow_run_id)

