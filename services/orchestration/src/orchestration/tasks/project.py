"""
Re-parses the day's raw RSS and lands anything new into the intraday projection.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.bronze.dividend_text import filing_texts
from pipeline.bronze.ingest import client
from pipeline.bronze.news import day_items, tag_items
from pipeline.config import get_settings
from pipeline.gold.cash_dividend_news import read_items as read_cash_dividend_items
from pipeline.gold.corporate_action_news import read_items as read_corporate_action_items
from pipeline.reference.matcher import Registry
from pipeline.reference.store import read as read_registry

from orchestration.config import OrchestrationConfig
from orchestration.projection import (
    pending_count,
    pending_filing_count,
    prune,
    upsert_filings,
    upsert_items,
)
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


@task(name="project_corporate_actions")
def project_corporate_actions(trade_date: date) -> PhaseResult:
    """Queues the window's corporate actions for scoring, the same way articles are."""
    settings = get_settings()
    dsn = settings.postgres_dsn
    items = read_corporate_action_items(trade_date, settings)
    if not items:
        return PhaseResult(status="SKIPPED", notes="no corporate actions in window")
    outcome = upsert_items(dsn, items, trade_date, None)
    return PhaseResult(
        status="SUCCESS",
        payload={"new_items": outcome.new_item_ids, "tickers": outcome.new_tickers},
        notes=f"{len(items)} corporate actions in window, {len(outcome.new_item_ids)} new",
    )


@task(name="project_cash_dividend_news")
def project_cash_dividend_news(trade_date: date) -> PhaseResult:
    """Queues the window's filed dividends for scoring, the same way articles are."""
    settings = get_settings()
    dsn = settings.postgres_dsn
    items = read_cash_dividend_items(trade_date, settings)
    if not items:
        return PhaseResult(status="SKIPPED", notes="no filed dividends in window")
    outcome = upsert_items(dsn, items, trade_date, None)
    return PhaseResult(
        status="SUCCESS",
        payload={"new_items": outcome.new_item_ids, "tickers": outcome.new_tickers},
        notes=f"{len(items)} filed dividends in window, {len(outcome.new_item_ids)} new",
    )


@task(name="project_dividend_filings")
def project_dividend_filings(trade_date: date, ingest_run_id: str | None = None) -> PhaseResult:
    """Queues the day's readable filings so the extraction step can claim them."""
    settings = get_settings()
    dsn = settings.postgres_dsn
    readable = [row for row in filing_texts(client(settings), trade_date) if row["status"] == "EXTRACTED"]
    if not readable:
        return PhaseResult(status="SKIPPED", notes="no readable dividend filings")
    new_ids = upsert_filings(dsn, readable, trade_date, ingest_run_id)
    return PhaseResult(
        status="SUCCESS",
        payload={"new_filings": new_ids, "pending": pending_filing_count(dsn, trade_date)},
        notes=f"{len(readable)} readable filings, {len(new_ids)} new",
        ingest_run_id=ingest_run_id,
    )
