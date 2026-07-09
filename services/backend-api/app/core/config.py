from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config for the backend-api process, env-driven, no secrets hardcoded."""

    model_config = SettingsConfigDict(env_prefix="GLOOMBERG_", env_file=".env")

    host: str = "127.0.0.1"
    port: int = 8000
    web_terminal_origin: str = "http://localhost:3000"
    api_token: str = ""

    postgres_dsn: str = "postgresql://localhost:5432/gloomberg"
    duckdb_gold_path: str = "warehouse/gold.duckdb"

    pg_pool_min_size: int = 2
    pg_pool_max_size: int = 8


settings = Settings()
