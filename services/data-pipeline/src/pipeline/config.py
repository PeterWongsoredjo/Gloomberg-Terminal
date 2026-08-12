"""Env-driven settings for the medallion glue: MinIO, Postgres, and warehouse paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

# repo root is four levels up from this file (services/data-pipeline/src/pipeline)
REPO_ROOT = Path(__file__).resolve().parents[4]
BRONZE_BUCKET = "gloomberg-bronze"


def load_root_env() -> None:
    """Reads the repo-root .env into os.environ without clobbering what's already set."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    """Everything the loader/publisher/sync need to reach the local or VPS stack."""

    minio_endpoint: str
    minio_key: str
    minio_secret: str
    minio_secure: bool
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    build_duckdb: Path
    published_gold: Path
    webshare_proxy_url: str | None

    @property
    def postgres_dsn(self) -> str:
        """libpq DSN for the DuckDB postgres extension and verification clients."""
        return (
            f"dbname={self.postgres_db} host={self.postgres_host} "
            f"port={self.postgres_port} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


def _webshare_proxy_url() -> str | None:
    """Builds http://user:pass@host:port from the four WEBSHARE_PROXY_* vars, or None."""
    host = os.environ.get("WEBSHARE_PROXY_HOST")
    if not host:
        return None
    port = os.environ.get("WEBSHARE_PROXY_PORT", "80")
    user = quote(os.environ["WEBSHARE_PROXY_USERNAME"], safe="")
    password = quote(os.environ["WEBSHARE_PROXY_PASSWORD"], safe="")
    return f"http://{user}:{password}@{host}:{port}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Builds Settings from the environment once, loading the root .env first."""
    load_root_env()
    return Settings(
        minio_endpoint=os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        minio_key=os.environ["MINIO_KEY"],
        minio_secret=os.environ["MINIO_SECRET"],
        minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        postgres_host=os.environ.get("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        postgres_user=os.environ["POSTGRES_USER"],
        postgres_password=os.environ["POSTGRES_PASSWORD"],
        postgres_db=os.environ.get("POSTGRES_DB", "gloomberg"),
        build_duckdb=REPO_ROOT / "dbt" / "warehouse" / "build.duckdb",
        published_gold=REPO_ROOT / "services" / "backend-api" / "warehouse" / "gold.duckdb",
        webshare_proxy_url=_webshare_proxy_url(),
    )
