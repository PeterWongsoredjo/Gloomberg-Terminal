"""
Env-driven agentic settings: budgets, confidence gates, provider models and keys.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root is five levels up from app/agentic/config.py
_REPO_ROOT = Path(__file__).resolve().parents[4]


class AgenticSettings(BaseSettings):
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

    # providers retire models often, override with GROQ_MODEL or GEMINI_MODEL to re-pin
    # groq dropped the whole llama chat line in 2026, this is the pinned replacement
    groq_model: str = "qwen/qwen3.8-27b"
    # 2.5-flash-lite was retired for new users mid-2026, pin its stable successor
    gemini_model: str = "gemini-3.1-flash-lite"

    # budget caps
    max_loop_iterations: int = 3
    max_total_tokens: int = 60000

    # confidence gates
    sentiment_confidence_gate: float = 0.5
    extraction_confidence_gate: float = 0.5
    insight_confidence_gate: float = 0.45

    # circuit breaker
    breaker_failure_threshold: int = 5
    breaker_cooldown_seconds: float = 120.0

    # intraday scoring gives up on an item after this many runs
    intraday_max_score_attempts: int = 3

    # newest news items sent per subject, bounds prompt size on busy days
    max_news_per_subject: int = 12

    article_batch_size: int = 10
    article_items_per_poll: int = 40
    # one filing per request, so eight stays inside the run's token budget
    dividend_filings_per_run: int = 8
    dividend_filing_char_cap: int = 12000
    dividend_max_extract_attempts: int = 3
    article_backfill_batches_per_run: int = 15
    insight_subject_cap: int = 8

    # cache
    cache_ttl_hours: int = 20
    cache_staleness_budget_hours: int = 48

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    def has_gemini(self) -> bool:
        return bool(self.google_api_key)


@lru_cache(maxsize=1)
def get_agentic_settings() -> AgenticSettings:
    return AgenticSettings()
