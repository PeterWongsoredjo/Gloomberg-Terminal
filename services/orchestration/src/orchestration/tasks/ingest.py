from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from prefect import task

from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import client, emit_failed_manifest, fetch_and_land
from pipeline.bronze.loader import load_fixtures
from pipeline.config import get_settings

from orchestration.retries import (
    INGEST_BACKOFF,
    INGEST_JITTER,
    INGEST_RETRIES,
    retry_on_transient_fetch,
)


@task(
    name="ingest_feed",
    tags=["minio_fetch"],
    retries=INGEST_RETRIES,
    retry_delay_seconds=INGEST_BACKOFF,
    retry_jitter_factor=INGEST_JITTER,
    retry_condition_fn=retry_on_transient_fetch,
)
def ingest_feed(feed_name: str, trade_date: date) -> dict[str, Any]:
    settings = get_settings()
    return fetch_and_land(client(settings), FEEDS[feed_name], trade_date)


def land_failed(feed_name: str, trade_date: date, reason: str) -> dict[str, Any]:
    """Emits a FAILED manifest for a feed whose fetch gave up, no Bronze written."""
    settings = get_settings()
    return emit_failed_manifest(client(settings), FEEDS[feed_name], trade_date, reason)


@task(name="load_fixtures")
def load_fixtures_bronze(roots: list[str]) -> list[dict[str, Any]]:
    """Replays committed Bronze fixtures through the live land primitive (deterministic e2e)."""
    settings = get_settings()
    manifests: list[dict[str, Any]] = []
    for root in roots:
        path = Path(root)
        if path.exists():
            manifests.extend(load_fixtures(path, settings))
    return manifests
