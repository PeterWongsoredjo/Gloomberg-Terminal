"""
pins MLflow tracking to a single local directory to keep run histories in one place
verifies that the resolved tracking path matches the configured file store location
"""

from __future__ import annotations

import os

from app.observability.config import ObservabilitySettings, get_observability_settings


class MlflowUriMismatch(RuntimeError):
    """raised when the actual MLflow tracking path does not match the configured path"""


def configure_mlflow(settings: ObservabilitySettings | None = None) -> str:
    """sets the tracking destination and verifies it was successfully applied by MLflow"""
    resolved = settings or get_observability_settings()
    mandated = resolved.mlflow_tracking_uri
    os.environ["MLFLOW_TRACKING_URI"] = mandated
    # ADR-004 mandates the local file store; MLflow 3 gates it behind this opt-out flag
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import mlflow

    mlflow.set_tracking_uri(mandated)
    actual = mlflow.get_tracking_uri()
    if actual != mandated:
        raise MlflowUriMismatch(f"MLflow tracking URI is {actual!r}, not the mandated {mandated!r}")
    return mandated

