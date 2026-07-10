"""
calculates evaluation metrics by comparing predictions against the golden set.
measures sentiment accuracy, schema validity, ticker hallucination, and run costs
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.eval.golden import GoldenSet


@dataclass(frozen=True)
class Prediction:
    item_id: str
    label: str | None  # None means the response failed validation
    score: float | None
    evidence_item_ids: tuple[str, ...] = ()
    named_tickers: tuple[str, ...] = ()
    advisory: bool = False
    notional_cost: float = 0.0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class EvalMetrics:
    """aggregated accuracy, cost, and latency metrics logged during evaluation"""

    n: int
    label_agreement: float
    mae_score: float
    schema_valid_rate: float
    grounding_rate: float
    hallucinated_ticker_rate: float
    non_advisory_violations: int
    mean_notional_cost: float
    mean_latency_ms: float
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    diffs: list[dict[str, object]] = field(default_factory=list)


_LABELS = ("BEARISH", "NEUTRAL", "BULLISH")


def score_predictions(golden: GoldenSet, predictions: dict[str, Prediction]) -> EvalMetrics:
    """compares each predicted label and ticker to the golden set truth to build final metrics"""
    total = len(golden.items)
    universe = golden.universe
    agree = 0
    valid = 0
    grounded = 0
    hallucinated = 0
    advisory_violations = 0
    abs_errors: list[float] = []
    costs: list[float] = []
    latencies: list[float] = []
    confusion = {g: {p: 0 for p in _LABELS} for g in _LABELS}
    diffs: list[dict[str, object]] = []

    for item in golden.items:
        pred = predictions.get(item.item_id)
        costs.append(pred.notional_cost if pred else 0.0)
        latencies.append(pred.latency_ms if pred else 0.0)
        if pred is None or pred.label is None:
            diffs.append({"item_id": item.item_id, "gold": item.label, "pred": None, "trap": item.trap})
            continue
        valid += 1
        if pred.label == item.label:
            agree += 1
        if pred.label in _LABELS:
            confusion[item.label][pred.label] += 1
        if pred.score is not None:
            abs_errors.append(abs(pred.score - item.score))
        if set(pred.evidence_item_ids).issubset({item.item_id}):
            grounded += 1
        if any(ticker not in universe for ticker in pred.named_tickers):
            hallucinated += 1
        if pred.advisory:
            advisory_violations += 1
        if pred.label != item.label:
            diffs.append({"item_id": item.item_id, "gold": item.label, "pred": pred.label, "trap": item.trap})

    return EvalMetrics(
        n=total,
        label_agreement=agree / total if total else 0.0,
        mae_score=sum(abs_errors) / len(abs_errors) if abs_errors else 0.0,
        schema_valid_rate=valid / total if total else 0.0,
        grounding_rate=grounded / valid if valid else 0.0,
        hallucinated_ticker_rate=hallucinated / valid if valid else 0.0,
        non_advisory_violations=advisory_violations,
        mean_notional_cost=sum(costs) / total if total else 0.0,
        mean_latency_ms=sum(latencies) / total if total else 0.0,
        confusion=confusion,
        diffs=diffs,
    )

