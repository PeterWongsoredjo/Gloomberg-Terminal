from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root is five levels up from app/core/config.py
_REPO_ROOT = Path(__file__).resolve().parents[4]


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

    pg_pool_min_size: int = 2
    pg_pool_max_size: int = 8


settings = Settings()
