"""
Lands the two Bronze datasets the Gold models read but nothing was writing.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.agentic.artifact_land import land_artifacts
from pipeline.bronze.ingest import client
from pipeline.bronze.news import normalize_from_bronze
from pipeline.config import get_settings

from orchestration.results import PhaseResult


@task(name="normalize_news")
def normalize_news(trade_date: date) -> PhaseResult:
    """Lands the day's parsed articles where the Gold news models can read them."""
    settings = get_settings()
    manifest = normalize_from_bronze(client(settings), trade_date)
    return PhaseResult(
        status="SUCCESS",
        payload=manifest,
        notes=f"{manifest['record_count']} news items normalized to bronze",
        ingest_run_id=str(manifest.get("ingest_run_id") or "") or None,
    )


@task(name="land_artifacts")
def land_agent_artifacts(trade_date: date) -> PhaseResult:
    """Lands agent artifacts to Bronze so the Gold sentiment models have a source."""
    settings = get_settings()
    manifests = land_artifacts(client(settings), trade_date, settings)
    landed = sum(int(m.get("record_count") or 0) for m in manifests)
    return PhaseResult(
        status="SUCCESS",
        payload=manifests,
        notes=f"{landed} agent artifacts landed across {len(manifests)} datasets",
    )
