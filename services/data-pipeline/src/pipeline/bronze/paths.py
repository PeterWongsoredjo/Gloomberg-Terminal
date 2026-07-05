"""Builds the CT-002 Hive object keys and the CT-003 manifest key for Bronze."""

from __future__ import annotations

from datetime import date


def object_key(
    source: str,
    dataset: str,
    trade_date: date,
    source_version: str,
    ingest_run_id: str,
    seq: int,
    ext: str = "json",
) -> str:
    """Full Bronze object key for one payload part, Hive-partitioned per CT-002."""
    return (
        f"{source}/{dataset}/"
        f"ingest_date={trade_date.isoformat()}/"
        f"source_version={source_version}/"
        f"part-{ingest_run_id}-{seq:04d}.{ext}.zst"
    )


def manifest_key(source: str, dataset: str, trade_date: date, ingest_run_id: str) -> str:
    """Key for the one CT-003 manifest emitted per ingestion run."""
    return (
        f"_manifests/{source}/{dataset}/"
        f"ingest_date={trade_date.isoformat()}/{ingest_run_id}.json"
    )
