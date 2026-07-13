"""runs a prompt template over the golden set and saves the run data in MLflow
pins tracking storage, scores each model prediction, and records the run metrics
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agentic.nodes._common import contains_advice
from app.agentic.prompts.registry import PromptTemplate
from app.agentic.providers.base import ProviderError, ProviderRequest
from app.agentic.providers.ladder import ProviderLadder
from app.agentic.schemas import SentimentValue
from app.eval.golden import GoldenItem, load_golden_set
from app.eval.metrics import EvalMetrics, Prediction, score_predictions
from app.eval.mlflow_setup import configure_mlflow
from app.observability.config import ObservabilitySettings
from app.observability.cost import CostModel, get_cost_model

Predictor = Callable[[GoldenItem], Prediction]


@dataclass(frozen=True)
class EvalResult:
    """metrics and run metadata stored in MLflow for an evaluation run"""

    prompt_version: str
    provider: str
    dataset_version: str
    content_sha256: str
    mlflow_run_id: str
    metrics: EvalMetrics


def run_offline_eval(
    *,
    prompt_version: str,
    provider: str,
    model: str,
    predict: Predictor,
    dataset_version: str = "golden-2026Q2",
    decoding: dict[str, Any] | None = None,
    settings: ObservabilitySettings | None = None,
    experiment: str = "sentiment_prompt_eval",
) -> EvalResult:
    """loads golden set, collects predictions, scores them, and logs results to MLflow"""
    configure_mlflow(settings)
    import mlflow

    golden = load_golden_set(dataset_version)
    predictions = {item.item_id: predict(item) for item in golden.items}
    metrics = score_predictions(golden, predictions)

    mlflow.set_experiment(experiment)
    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                "prompt_version": prompt_version,
                "provider": provider,
                "model": model,
                "dataset_version": dataset_version,
                "content_sha256": golden.content_sha256,
                **(decoding or {"temperature": 0.0, "seed": 42}),
            }
        )
        mlflow.log_metrics(
            {
                "label_agreement": metrics.label_agreement,
                "mae_score": metrics.mae_score,
                "schema_valid_rate": metrics.schema_valid_rate,
                "grounding_rate": metrics.grounding_rate,
                "hallucinated_ticker_rate": metrics.hallucinated_ticker_rate,
                "non_advisory_violations": metrics.non_advisory_violations,
                "mean_notional_cost": metrics.mean_notional_cost,
                "mean_latency_ms": metrics.mean_latency_ms,
            }
        )
        mlflow.log_dict(metrics.confusion, "confusion_matrix.json")
        mlflow.log_dict({"diffs": metrics.diffs}, "per_item_diffs.json")
        run_id = run.info.run_id

    return EvalResult(prompt_version, provider, dataset_version, golden.content_sha256, run_id, metrics)


def build_sentiment_predictor(
    ladder: ProviderLadder,
    prompt: PromptTemplate,
    cost_model: CostModel | None = None,
) -> Predictor:
    """constructs a predictor that runs items through the model provider ladder"""
    costs = cost_model or get_cost_model()

    def _predict(item: GoldenItem) -> Prediction:
        payload = {
            "subject": {"ticker": item.ticker, "security_id": None},
            "news_items": [{"item_id": item.item_id, "headline": item.headline, "body": item.body}],
        }
        request = ProviderRequest(
            objective=prompt.objective,
            prompt_version=prompt.version,
            system=prompt.system_contract,
            user=str(payload),
            response_model=SentimentValue,
            temperature=prompt.temperature,
            seed=prompt.seed,
            max_output_tokens=prompt.max_output_tokens,
        )
        try:
            response = asyncio.run(ladder.complete(request))
        except ProviderError:
            return Prediction(item_id=item.item_id, label=None, score=None)
        return _to_prediction(item, response, costs)

    return _predict


def _to_prediction(item: GoldenItem, response: Any, costs: CostModel) -> Prediction:
    """converts raw provider response to a structured prediction and calculates notional costs"""
    if response.parsed is None:
        return Prediction(item_id=item.item_id, label=None, score=None)
    try:
        value = SentimentValue.model_validate(response.parsed)
    except ValidationError:
        return Prediction(item_id=item.item_id, label=None, score=None)
    return Prediction(
        item_id=item.item_id,
        label=value.sentiment_label,
        score=value.sentiment_score,
        evidence_item_ids=tuple(value.evidence_item_ids),
        named_tickers=(),
        advisory=contains_advice(" ".join(value.drivers)),
        notional_cost=costs.notional_cost(response.provider, response.prompt_tokens, response.completion_tokens),
        latency_ms=float(response.latency_ms),
    )

