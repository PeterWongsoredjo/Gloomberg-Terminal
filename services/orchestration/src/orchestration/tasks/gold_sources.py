"""
Lands the two Bronze datasets the Gold models read but nothing was writing.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.agentic.artifact_land import land_artifacts, unlanded_dates
from pipeline.bronze.ingest import client
from pipeline.bronze.news import normalize_from_bronze, unnormalized_dates
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


@task(name="reconcile_news_items")
def reconcile_news_items() -> PhaseResult:
    """Parses every captured day the normalizer never got to."""
    minio = client(get_settings())
    days = unnormalized_dates(minio)
    if not days:
        return PhaseResult(status="SKIPPED", notes="every captured day is already normalized")
    items = sum(int(normalize_from_bronze(minio, day).get("record_count") or 0) for day in days)
    return PhaseResult(
        status="SUCCESS",
        payload={"dates": [d.isoformat() for d in days], "items": items},
        notes=f"{items} news items normalized across {len(days)} missed days",
    )


@task(name="reconcile_agent_artifacts")
def reconcile_agent_artifacts() -> PhaseResult:
    """Lands every scored day still sitting in the ledger and not in Bronze."""
    settings = get_settings()
    minio = client(settings)
    days = unlanded_dates(minio, settings)
    if not days:
        return PhaseResult(status="SKIPPED", notes="every scored day is already landed")
    landed = 0
    for day in days:
        landed += sum(int(m.get("record_count") or 0) for m in land_artifacts(minio, day, settings))
    return PhaseResult(
        status="SUCCESS",
        payload={"dates": [d.isoformat() for d in days], "artifacts": landed},
        notes=f"{landed} agent artifacts landed across {len(days)} missed days",
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
