"""
Re-parses the day's raw RSS and lands anything new into the intraday projection.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.bronze.ingest import client
from pipeline.bronze.news import day_items
from pipeline.config import get_settings

from orchestration.config import OrchestrationConfig
from orchestration.projection import prune, upsert_items
from orchestration.results import PhaseResult


@task(name="project_news")
def project_news(trade_date: date, config: OrchestrationConfig, ingest_run_id: str | None) -> PhaseResult:
    settings = get_settings()
    items = day_items(client(settings), trade_date)
    outcome = upsert_items(settings.postgres_dsn, items, trade_date, ingest_run_id)
    prune(settings.postgres_dsn, trade_date)
    return PhaseResult(
        status="SUCCESS",
        payload={"new_items": outcome.new_item_ids, "tickers": outcome.new_tickers},
        notes=f"{len(items)} items parsed, {len(outcome.new_item_ids)} new",
        ingest_run_id=ingest_run_id,
    )
