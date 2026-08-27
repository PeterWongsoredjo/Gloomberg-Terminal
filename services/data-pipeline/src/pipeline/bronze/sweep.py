"""
Finds the day's feeds that never landed, so a later run can try them again.

The proxy blocks in stretches, so retries seconds apart all see the same bad
window. Retrying an hour later samples a different one, which is what actually
gets a blocked feed through.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from minio import Minio
from minio.error import S3Error

from pipeline.bronze import paths
from pipeline.bronze.feeds import EOD_FEEDS, FEEDS, FeedSpec
from pipeline.bronze.ingest import landing_date
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.clock import now_utc
from pipeline.config import BRONZE_BUCKET

LANDED = "SUCCESS"


def landed_on(spec: FeedSpec, trade_date: date) -> date:
    """The partition this feed would use for the date if it were fetched right now."""
    return landing_date(spec, trade_date, now_utc())


def read_manifest(
    minio: Minio, source: str, dataset: str, trade_date: date, source_version: str = "v1"
) -> dict[str, Any] | None:
    """The manifest a replaceable landing wrote for one date, or None when it never ran."""
    run_id = deterministic_run_id(idempotency_key(source, dataset, trade_date, source_version))
    key = paths.manifest_key(source, dataset, trade_date, run_id)
    try:
        response = minio.get_object(BRONZE_BUCKET, key)
    except S3Error:
        return None
    try:
        manifest: dict[str, Any] = json.loads(response.read())
    finally:
        response.close()
        response.release_conn()
    return manifest


def landed(minio: Minio, source: str, dataset: str, trade_date: date) -> bool:
    """Whether a dataset already has a clean landing for the date."""
    manifest = read_manifest(minio, source, dataset, trade_date)
    return manifest is not None and manifest.get("status") == LANDED


def feed_landed(minio: Minio, spec: FeedSpec, trade_date: date) -> bool:
    """Whether a feed already landed cleanly wherever a fetch now would put it."""
    return landed(minio, spec.source, spec.dataset, landed_on(spec, trade_date))


def unlanded_eod_feeds(minio: Minio, trade_date: date) -> list[str]:
    """The day's EOD feeds still worth another attempt, in registry order."""
    return [name for name in EOD_FEEDS if not feed_landed(minio, FEEDS[name], trade_date)]
