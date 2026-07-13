"""
environment settings for telemetry configuration including langfuse keys and thresholds
allows fallback to no-op tracing if settings or keys are missing from the env
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root is five levels up from app/observability/config.py
_REPO_ROOT = Path(__file__).resolve().parents[4]
# the one canonical mlruns tree, absolute, under this backend-api checkout (OB-07)
_BACKEND_API_DIR = Path(__file__).resolve().parents[2]
_MLRUNS_URI = (_BACKEND_API_DIR / "mlruns").as_uri()


class ObservabilitySettings(BaseSettings):
    """local configuration settings loaded from environment variables and dot env"""

    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    langfuse_public_key: str = Field(default="", validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    # the mandated absolute file URI; the eval harness asserts MLflow resolves to exactly this
    mlflow_tracking_uri: str = Field(default=_MLRUNS_URI, validation_alias="GLOOMBERG_MLFLOW_TRACKING_URI")

    # OB-04 feedback: at this quota fraction a provider is skipped before the hard 429
    quota_shift_threshold: float = 0.95

    # regression-gate tolerances (OB-08 §3.6): a candidate may not regress past these
    gate_agreement_tolerance: float = 0.05
    gate_hallucination_tolerance: float = 0.02
    gate_schema_tolerance: float = 0.05

    # §3.9 self-monitoring knobs
    spool_max_events: int = 10000
    heartbeat_interval_seconds: float = 60.0
    heartbeat_stale_after_seconds: float = 180.0

    def has_langfuse(self) -> bool:
        """returns true if public and secret keys are both provided for langfuse"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache(maxsize=1)
def get_observability_settings() -> ObservabilitySettings:
    """returns cached instance of observability settings"""
    return ObservabilitySettings()

