"""OB-07/OB-08: MLflow pins one canonical store, and the gate blocks a regressive prompt."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.eval.gate import regression_gate
from app.eval.harness import run_offline_eval
from app.eval.metrics import EvalMetrics, Prediction
from app.eval.mlflow_setup import MlflowUriMismatch, configure_mlflow
from app.observability.config import ObservabilitySettings


def _tmp_settings(tmp_path: Path) -> ObservabilitySettings:
    settings = ObservabilitySettings(_env_file=None)  # type: ignore[call-arg]
    settings.mlflow_tracking_uri = (tmp_path / "mlruns").as_uri()
    return settings


def test_configure_sets_and_returns_the_mandated_uri(tmp_path: Path) -> None:
    settings = _tmp_settings(tmp_path)
    uri = configure_mlflow(settings)
    assert uri == settings.mlflow_tracking_uri
    assert os.environ["MLFLOW_TRACKING_URI"] == uri


def test_configure_refuses_on_a_store_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _tmp_settings(tmp_path)
    import mlflow

    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: None)  # ignore the set, force a mismatch
    monkeypatch.setattr(mlflow, "get_tracking_uri", lambda: "file:///somewhere/else/mlruns")
    with pytest.raises(MlflowUriMismatch):
        configure_mlflow(settings)


def test_eval_writes_only_to_the_mandated_store_from_a_foreign_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _tmp_settings(tmp_path)
    foreign = tmp_path / "some_other_cwd"
    foreign.mkdir()
    monkeypatch.chdir(foreign)

    def perfect(item: object) -> Prediction:
        return Prediction(item.item_id, item.label, item.score, evidence_item_ids=(item.item_id,))  # type: ignore[attr-defined]

    result = run_offline_eval(prompt_version="sent-v4", provider="groq", model="m", predict=perfect, settings=settings)
    assert result.metrics.label_agreement == 1.0
    assert not (foreign / "mlruns").exists()  # no stray mlruns in the launch dir
    assert (tmp_path / "mlruns").exists()  # only the mandated store got written


def _metrics(agreement: float, hallucination: float = 0.0, schema: float = 1.0, advisory: int = 0) -> EvalMetrics:
    return EvalMetrics(
        n=12, label_agreement=agreement, mae_score=0.1, schema_valid_rate=schema, grounding_rate=1.0,
        hallucinated_ticker_rate=hallucination, non_advisory_violations=advisory, mean_notional_cost=0.001, mean_latency_ms=500,
    )


def test_gate_rejects_a_regressive_candidate() -> None:
    live = _metrics(agreement=0.9)
    candidate = _metrics(agreement=0.6)  # a 0.3 drop, well past tolerance
    result = regression_gate(candidate, live)
    assert result.passed is False
    assert any("label_agreement" in reason for reason in result.reasons)


def test_gate_rejects_any_advisory_violation() -> None:
    result = regression_gate(_metrics(agreement=0.95, advisory=3), _metrics(agreement=0.9))
    assert result.passed is False


def test_first_prompt_passes_without_a_baseline() -> None:
    result = regression_gate(_metrics(agreement=0.8), None)
    assert result.passed is True
