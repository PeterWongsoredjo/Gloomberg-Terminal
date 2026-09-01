"""Env-driven orchestration config so the Cloud/VPS switch is a .env flip, never a code edit."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.config import REPO_ROOT, load_root_env
from pipeline.quality.universe import MIN_PLAUSIBLE_SECURITIES

from orchestration.universe import load_universe

# the hand-curated ticker list every scoring flow reads
DEFAULT_UNIVERSE_FILE = REPO_ROOT / "services" / "orchestration" / "config" / "universe.yaml"


@dataclass(frozen=True)
class OrchestrationConfig:
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
    # below this an IDX profile payload is broken, not a small market
    universe_min_securities: int = MIN_PLAUSIBLE_SECURITIES
    session_windows: Path = REPO_ROOT / "services" / "backend-api" / "app" / "observability" / "config" / "session_windows.yaml"
    intraday_objective: str = "article_sentiment"
    intraday_insight_objective: str = "intraday_insight"
    eod_insight_objective: str = "insight_synthesis"
    dividend_objective: str = "dividend_extraction"
    insight_subject_cap: int = 8
    eod_insight_subject_cap: int = 12
    universe_file: Path = DEFAULT_UNIVERSE_FILE
    subject_universe: list[str] = field(default_factory=list)


def get_config() -> OrchestrationConfig:
    load_root_env()
    universe_file = Path(os.environ.get("GLOOMBERG_ORCH_UNIVERSE_FILE", str(DEFAULT_UNIVERSE_FILE)))
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
        universe_min_securities=int(
            os.environ.get("GLOOMBERG_ORCH_UNIVERSE_MIN_SECURITIES", str(MIN_PLAUSIBLE_SECURITIES))
        ),
        ingest_mode=os.environ.get("GLOOMBERG_ORCH_INGEST_MODE", "live").lower(),
        backend_api_url=os.environ.get("GLOOMBERG_ORCH_BACKEND_API_URL", "http://127.0.0.1:8000"),
        backend_api_token=os.environ.get("GLOOMBERG_ORCH_BACKEND_API_TOKEN", ""),
        poll_interval_seconds=float(os.environ.get("GLOOMBERG_ORCH_POLL_INTERVAL", "5")),
        poll_timeout_seconds=float(os.environ.get("GLOOMBERG_ORCH_POLL_TIMEOUT", "600")),
        trigger_timeout_seconds=float(os.environ.get("GLOOMBERG_ORCH_TRIGGER_TIMEOUT", "10")),
        session_windows=Path(
            os.environ.get(
                "GLOOMBERG_ORCH_SESSION_WINDOWS",
                str(
                    REPO_ROOT / "services" / "backend-api" / "app"
                    / "observability" / "config" / "session_windows.yaml"
                ),
            )
        ),
        intraday_objective=os.environ.get("GLOOMBERG_ORCH_INTRADAY_OBJECTIVE", "article_sentiment"),
        intraday_insight_objective=os.environ.get(
            "GLOOMBERG_ORCH_INTRADAY_INSIGHT_OBJECTIVE", "intraday_insight"
        ),
        eod_insight_objective=os.environ.get(
            "GLOOMBERG_ORCH_EOD_INSIGHT_OBJECTIVE", "insight_synthesis"
        ),
        dividend_objective=os.environ.get(
            "GLOOMBERG_ORCH_DIVIDEND_OBJECTIVE", "dividend_extraction"
        ),
        insight_subject_cap=int(os.environ.get("GLOOMBERG_ORCH_INSIGHT_SUBJECT_CAP", "8")),
        eod_insight_subject_cap=int(os.environ.get("GLOOMBERG_ORCH_EOD_INSIGHT_SUBJECT_CAP", "12")),
        universe_file=universe_file,
        subject_universe=load_universe(universe_file),
    )
