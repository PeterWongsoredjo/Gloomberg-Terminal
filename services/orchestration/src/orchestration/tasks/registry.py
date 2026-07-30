"""
Rebuilds the IDX security registry from Bronze, and re-tags anything it changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from prefect import task

from pipeline.bronze.ingest import client
from pipeline.config import get_settings
from pipeline.reference import store
from pipeline.reference.matcher import Registry
from pipeline.reference.refresh import refresh as refresh_from_bronze

from orchestration.projection import retag_items
from orchestration.results import PhaseResult


@task(name="refresh_registry")
def refresh_registry() -> PhaseResult:
    """Refreshes the registry, degrading to the committed baseline rather than emptying it."""
    settings = get_settings()
    outcome = refresh_from_bronze(client(settings), settings.postgres_dsn)
    return PhaseResult(
        status="DEGRADED" if outcome.degraded else "SUCCESS",
        payload={"count": outcome.count, "source": outcome.source},
        notes=f"{outcome.count} securities from {outcome.source}: {outcome.notes}",
    )


@task(name="retag_news")
def retag_news() -> PhaseResult:
    """Re-resolves stored articles whose tags predate the newest registry."""
    settings = get_settings()
    dsn = settings.postgres_dsn
    stamp = store.refreshed_at(dsn) or datetime.now(timezone.utc)
    registry = Registry(store.read(dsn))
    retagged = retag_items(dsn, registry, stamp)
    return PhaseResult(
        status="SUCCESS",
        payload={"retagged": retagged},
        notes=f"{retagged} articles re-tagged against {len(registry)} known securities",
    )
