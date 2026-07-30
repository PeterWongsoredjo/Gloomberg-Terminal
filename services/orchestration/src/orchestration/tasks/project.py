"""
Re-parses the day's raw RSS and lands anything new into the intraday projection.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.bronze.ingest import client
from pipeline.bronze.news import day_items, tag_items
from pipeline.config import get_settings
from pipeline.reference.matcher import Registry
from pipeline.reference.store import read as read_registry

from orchestration.config import OrchestrationConfig
from orchestration.projection import pending_count, prune, upsert_items
from orchestration.results import PhaseResult


@task(name="project_news")
def project_news(trade_date: date, config: OrchestrationConfig, ingest_run_id: str | None) -> PhaseResult:
    settings = get_settings()
    dsn = settings.postgres_dsn
    registry = Registry(read_registry(dsn))
    items = tag_items(day_items(client(settings), trade_date), registry)
    outcome = upsert_items(dsn, items, trade_date, ingest_run_id)
    prune(dsn, trade_date)
    return PhaseResult(
        status="SUCCESS",
        payload={
            "new_items": outcome.new_item_ids,
            "tickers": outcome.new_tickers,
            "pending": pending_count(dsn, trade_date),
        },
        notes=f"{len(items)} items parsed, {len(outcome.new_item_ids)} new",
        ingest_run_id=ingest_run_id,
    )
