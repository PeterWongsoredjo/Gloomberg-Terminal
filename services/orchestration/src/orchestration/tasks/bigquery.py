"""
Mirrors the day's Gold into BigQuery for analysis off the box.
"""

from __future__ import annotations

from prefect import task

from pipeline.bigquery.mirror import publish_mirror
from pipeline.bigquery.target import target_from_env
from pipeline.config import get_settings

from orchestration.results import PhaseResult


@task(name="publish_bigquery")
def publish_bigquery() -> PhaseResult:
    """Mirrors the published snapshot, skipping cleanly when BigQuery is off."""
    target = target_from_env()
    if target is None:
        return PhaseResult(status="SKIPPED", notes="BQ_PROJECT not set, BigQuery mirror is off")
    manifest = publish_mirror(target, get_settings().published_gold)
    rows = sum(int(row["row_count"]) for row in manifest)
    return PhaseResult(
        status="SUCCESS",
        payload=manifest,
        notes=f"{rows} rows mirrored across {len(manifest)} Gold tables",
    )
