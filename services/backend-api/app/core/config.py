from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config for the backend-api process, env-driven, no secrets hardcoded."""

    model_config = SettingsConfigDict(env_prefix="GLOOMBERG_", env_file=".env")

    host: str = "127.0.0.1"  # loopback only, no public ingress (SV-10)
    port: int = 8000
    web_terminal_origin: str = "http://localhost:3000"  # CORS allowlist (SV-10)
    api_token: str = ""  # stub operator token; empty means loopback-trust (SV-10)

    postgres_dsn: str = "postgresql://localhost:5432/gloomberg"
    duckdb_gold_path: str = "warehouse/gold.duckdb"

    pg_pool_min_size: int = 2
    pg_pool_max_size: int = 8  # bounded under the 4-vCPU cap (SV-01)


settings = Settings()
