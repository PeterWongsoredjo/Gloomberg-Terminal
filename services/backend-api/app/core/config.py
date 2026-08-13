from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root is five levels up from app/core/config.py
_REPO_ROOT = Path(__file__).resolve().parents[4]
# the curated ticker file, owned by orchestration and read here as-is
_UNIVERSE_FILE = _REPO_ROOT / "services" / "orchestration" / "config" / "universe.yaml"


class Settings(BaseSettings):
    """Runtime config for the backend-api process, env-driven, no secrets hardcoded."""

    model_config = SettingsConfigDict(
        env_prefix="GLOOMBERG_", env_file=_REPO_ROOT / ".env", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000
    web_terminal_origin: str = "http://localhost:3000"
    api_token: str = ""
    # per visitor IP, generous for browser polling, hostile to scripted bursts
    rate_limit: str = "240/minute"

    postgres_dsn: str = "postgresql://localhost:5432/gloomberg"
    duckdb_gold_path: str = "warehouse/gold.duckdb"

    # both names are honoured so a moved file cannot drift from the flows
    universe_file: str = Field(
        default=str(_UNIVERSE_FILE),
        validation_alias=AliasChoices("GLOOMBERG_UNIVERSE_FILE", "GLOOMBERG_ORCH_UNIVERSE_FILE"),
    )

    pg_pool_min_size: int = 2
    pg_pool_max_size: int = 8


settings = Settings()
