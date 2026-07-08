"""Env-driven agentic settings: budgets, confidence gates, provider models and keys.

Provider limits and gates are config, not code constants, mirroring how the platform treats
every volatile external parameter. The repo-root .env holds the real provider keys; both the
GEMINI_API_KEY and GOOGLE_AI_STUDIO_API_KEY spellings are accepted so either works.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root is five levels up from app/agentic/config.py
_REPO_ROOT = Path(__file__).resolve().parents[4]


class AgenticSettings(BaseSettings):
    """Everything the graph needs to run against the free-tier providers."""

    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    groq_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")
    google_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY"),
    )

    # Postgres reaches the same container instance the pipeline and orchestration use
    postgres_host: str = Field(default="127.0.0.1", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_user: str = Field(default="gloomberg", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="", validation_alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="gloomberg", validation_alias="POSTGRES_DB")

    groq_model: str = "llama-3.3-70b-versatile"
    groq_fallback_model: str = "llama-3.1-8b-instant"
    gemini_model: str = "gemini-2.5-flash-lite"

    # CT-010 default budget caps; a run may tighten but never loosen these
    max_loop_iterations: int = 3
    max_total_tokens: int = 60000

    # confidence gates per objective; below the gate an artifact is LLM_LOW_CONFIDENCE
    sentiment_confidence_gate: float = 0.5
    extraction_confidence_gate: float = 0.5
    insight_confidence_gate: float = 0.45

    # AG-06 circuit breaker (effective config, not hardcoded constants)
    breaker_failure_threshold: int = 5
    breaker_window_seconds: float = 60.0
    breaker_cooldown_seconds: float = 120.0

    # AG-08 cache: happy-path reuse window and the wider Tier-2 staleness budget
    cache_ttl_hours: int = 20
    cache_staleness_budget_hours: int = 48

    @property
    def postgres_dsn(self) -> str:
        """libpq URL for asyncpg, psycopg, and the checkpointer."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def has_groq(self) -> bool:
        """True when a real Groq key is present, so live calls are possible."""
        return bool(self.groq_api_key)

    def has_gemini(self) -> bool:
        """True when a real Gemini key is present, so live calls are possible."""
        return bool(self.google_api_key)


@lru_cache(maxsize=1)
def get_agentic_settings() -> AgenticSettings:
    """Builds the agentic settings once from the environment and root .env."""
    return AgenticSettings()
