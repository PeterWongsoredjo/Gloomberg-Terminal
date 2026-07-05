"""OR-01 phase 6: publish Gold and refresh the agg_* projections, atomically.

Both the DuckDB snapshot swap and the Postgres refresh are Stage-1 functions; here we only
sequence them under the held duckdb_writer lease (ADR-001). A failure here leaves the prior
Gold live, because the swap is a single os.replace and the sync is one transaction.
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
