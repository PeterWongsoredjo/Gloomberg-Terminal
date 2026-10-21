from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import zstandard
from minio import Minio

from pipeline.bronze.core.feeds import FeedSpec
from pipeline.bronze.core.http import fetch, url_for
from pipeline.bronze.core.manifest import build_manifest
from pipeline.bronze.core.policy import (
    landing_date,
    landing_verdict,
    record_count,
    run_id_for,
    upstream_record_total,
)
from pipeline.bronze.core.store import manifest_key, object_key, put
from pipeline.clock import now_utc


def write_manifest(minio: Minio, manifest: dict[str, Any]) -> None:
    key = manifest_key(
        manifest["source"],
        manifest["dataset"],
        date.fromisoformat(manifest["trade_date"]),
        manifest["ingest_run_id"],
    )
    put(minio, key, json.dumps(manifest, indent=2).encode(), "application/json")


def land_payloads(
    minio: Minio,
    *,
    source: str,
    dataset: str,
    trade_date: date,
    source_version: str,
    ingest_run_id: str,
    payloads: list[bytes],
    record_count: int,
    requested_at: datetime,
    expected_universe: int | None = None,
    missing_tickers: list[str] | None = None,
    declared_record_total: int | None = None,
    ext: str = "json",
    status: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    compressor = zstandard.ZstdCompressor()
    for seq, raw in enumerate(payloads):
        key = object_key(source, dataset, trade_date, source_version, ingest_run_id, seq, ext)
        put(minio, key, compressor.compress(raw), "application/zstd")

    manifest = build_manifest(
        ingest_run_id=ingest_run_id,
        source=source,
        dataset=dataset,
        trade_date=trade_date,
        source_version=source_version,
        payloads=payloads,
        record_count=record_count,
        expected_universe=expected_universe,
        missing_tickers=missing_tickers,
        declared_record_total=declared_record_total,
        requested_at=requested_at,
        status=status,
        notes=notes,
    )
    write_manifest(minio, manifest)
    return manifest


def fetch_and_land(
    minio: Minio, spec: FeedSpec, trade_date: date, *, proxy: str | None = None
) -> dict[str, Any]:
    captured_at = now_utc()
    raw = fetch(url_for(spec, trade_date), proxy=proxy if spec.needs_proxy else None)
    landed_on = landing_date(spec, trade_date, captured_at)
    count = record_count(raw, spec.ext)
    declared = upstream_record_total(raw, spec.ext)
    status, notes = landing_verdict(spec, count, declared)
    return land_payloads(
        minio,
        source=spec.source,
        dataset=spec.dataset,
        trade_date=landed_on,
        source_version=spec.source_version,
        ingest_run_id=run_id_for(spec, landed_on),
        payloads=[raw],
        record_count=count,
        declared_record_total=declared,
        requested_at=captured_at,
        ext=spec.ext,
        status=status,
        notes=notes,
    )


def emit_failed_manifest(minio: Minio, spec: FeedSpec, trade_date: date, reason: str) -> dict[str, Any]:
    captured_at = now_utc()
    landed_on = landing_date(spec, trade_date, captured_at)
    manifest = build_manifest(
        ingest_run_id=run_id_for(spec, landed_on),
        source=spec.source,
        dataset=spec.dataset,
        trade_date=landed_on,
        source_version=spec.source_version,
        payloads=[],
        record_count=0,
        requested_at=captured_at,
        status="FAILED",
        notes=reason,
    )
    write_manifest(minio, manifest)
    return manifest
