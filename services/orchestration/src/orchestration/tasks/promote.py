"""
Swaps the newly generated Gold DuckDB file into production and 
mirrors key aggregates to Postgres.
"""

from __future__ import annotations

from prefect import task

from pipeline.config import get_settings
from pipeline.gold.pg_sync import sync
from pipeline.gold.publish import publish

from orchestration.results import PhaseResult


@task(name="promote_gold", tags=["duckdb_writer"])
def promote_gold() -> PhaseResult:
    """Swaps in the fresh Gold snapshot and mirrors the agg_* projections to Postgres."""
    settings = get_settings()
    published = publish(settings)
    synced = sync(settings)
    notes = f"published {len(published)} Gold tables; synced {len(synced)} projections"
    return PhaseResult(
        status="SUCCESS",
        payload={"published": published, "synced": synced},
        notes=notes,
    )
