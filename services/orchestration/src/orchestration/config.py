"""Env-driven orchestration config so the Cloud/VPS switch is a .env flip, never a code edit."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.config import REPO_ROOT, load_root_env

# the four dense-liquid tickers a daily_sentiment run defaults to when none are configured
DEFAULT_UNIVERSE = ["BBCA", "BBRI", "TLKM", "ASII"]


@dataclass(frozen=True)
class OrchestrationConfig:
    """Everything the daily flow needs, all overridable from the environment."""

    dbt_dir: Path
    calendar_seed: Path
    coverage_floor: float
    coverage_hard_min: float
    ingest_mode: str  # "live" fetches upstream; "fixture" replays committed Bronze fixtures
    backend_api_url: str
    backend_api_token: str
    poll_interval_seconds: float
    poll_timeout_seconds: float
    trigger_timeout_seconds: float
    eod_cron: str
    objective: str
    subject_universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))


def _universe() -> list[str]:
    """Reads a comma-separated subject universe, falling back to the liquid default."""
    raw = os.environ.get("GLOOMBERG_ORCH_UNIVERSE", "").strip()
    return [t.strip().upper() for t in raw.split(",") if t.strip()] or list(DEFAULT_UNIVERSE)


def get_config() -> OrchestrationConfig:
    """Builds the orchestration config from the environment, loading the root .env first."""
    load_root_env()
    return OrchestrationConfig(
        dbt_dir=Path(os.environ.get("GLOOMBERG_ORCH_DBT_DIR", str(REPO_ROOT / "dbt"))),
        calendar_seed=Path(
            os.environ.get(
                "GLOOMBERG_ORCH_CALENDAR_SEED",
                str(REPO_ROOT / "dbt" / "seeds" / "ref_market_calendar.csv"),
            )
        ),
        coverage_floor=float(os.environ.get("GLOOMBERG_ORCH_COVERAGE_FLOOR", "0.95")),
        coverage_hard_min=float(os.environ.get("GLOOMBERG_ORCH_COVERAGE_HARD_MIN", "0.80")),
        ingest_mode=os.environ.get("GLOOMBERG_ORCH_INGEST_MODE", "live").lower(),
        backend_api_url=os.environ.get("GLOOMBERG_ORCH_BACKEND_API_URL", "http://127.0.0.1:8000"),
        backend_api_token=os.environ.get("GLOOMBERG_ORCH_BACKEND_API_TOKEN", ""),
        poll_interval_seconds=float(os.environ.get("GLOOMBERG_ORCH_POLL_INTERVAL", "5")),
        poll_timeout_seconds=float(os.environ.get("GLOOMBERG_ORCH_POLL_TIMEOUT", "600")),
        trigger_timeout_seconds=float(os.environ.get("GLOOMBERG_ORCH_TRIGGER_TIMEOUT", "10")),
        eod_cron=os.environ.get("GLOOMBERG_ORCH_EOD_CRON", "0 17 * * 1-5"),
        objective=os.environ.get("GLOOMBERG_ORCH_OBJECTIVE", "daily_sentiment"),
        subject_universe=_universe(),
    )
