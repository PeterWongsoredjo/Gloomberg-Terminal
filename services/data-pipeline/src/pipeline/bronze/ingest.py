"""Lands payloads into MinIO Bronze under CT-002 paths with CT-003 manifests.

The land primitive is shared by the live daily ingest (the conductor calls it per feed) and
the fixture loader, so both write Bronze the exact same way. A live fetch uses a ULID run id;
a total fetch failure still emits a FAILED manifest so no partial Bronze is ever promoted.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone
from typing import Any

import zstandard
from curl_cffi import requests
from minio import Minio
from ulid import ULID

from pipeline.bronze import paths
from pipeline.bronze.feeds import FeedSpec
from pipeline.bronze.manifest import build_manifest
from pipeline.config import BRONZE_BUCKET, Settings


class FetchError(RuntimeError):
    """Upstream fetch failed; status_code is None for a connect/read timeout."""

    def __init__(self, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def client(settings: Settings) -> Minio:
    """MinIO S3 client against the host-mapped gateway."""
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_key,
        secret_key=settings.minio_secret,
        secure=settings.minio_secure,
    )


def _put(minio: Minio, key: str, data: bytes, content_type: str) -> None:
    """Uploads one immutable object to the Bronze bucket."""
    minio.put_object(
        BRONZE_BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def fetch(url: str) -> bytes:
    """Fetches a Cloudflare-protected endpoint with a browser fingerprint, raising typed."""
    try:
        response = requests.get(url, impersonate="chrome", timeout=30)
    except Exception as exc:  # curl_cffi network errors are connect/read timeouts
        raise FetchError(None, f"{url} -> {exc}") from exc
    if response.status_code != 200:
        raise FetchError(response.status_code, f"{url} -> HTTP {response.status_code}")
    content: bytes = response.content
    return content


def record_count(raw: bytes, ext: str) -> int:
    """Best-effort record count for the manifest, per payload shape."""
    if ext == "xml":
        return raw.count(b"<item")
    parsed: Any = json.loads(raw)
    if isinstance(parsed, list):
        return len(parsed)
    for field in ("data", "Replies", "results", "Items"):
        value = parsed.get(field) if isinstance(parsed, dict) else None
        if isinstance(value, list):
            return len(value)
    return 1


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
    expected_universe: int,
    missing_tickers: list[str],
    requested_at: datetime,
    ext: str = "json",
) -> dict[str, Any]:
    """Compresses each payload into Bronze and writes the one CT-003 manifest."""
    compressor = zstandard.ZstdCompressor()
    for seq, raw in enumerate(payloads):
        key = paths.object_key(source, dataset, trade_date, source_version, ingest_run_id, seq, ext)
        _put(minio, key, compressor.compress(raw), "application/zstd")

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
        requested_at=requested_at,
    )
    mkey = paths.manifest_key(source, dataset, trade_date, ingest_run_id)
    _put(minio, mkey, json.dumps(manifest, indent=2).encode(), "application/json")
    return manifest


def fetch_and_land(minio: Minio, spec: FeedSpec, trade_date: date) -> dict[str, Any]:
    """Fetches one live feed and lands it in Bronze; raises FetchError on upstream failure."""
    url = spec.url.format(ymd=trade_date.strftime("%Y%m%d")) if spec.date_scoped else spec.url
    raw = fetch(url)
    count = record_count(raw, spec.ext)
    return land_payloads(
        minio,
        source=spec.source,
        dataset=spec.dataset,
        trade_date=trade_date,
        source_version=spec.source_version,
        ingest_run_id=str(ULID()),
        payloads=[raw],
        record_count=count,
        expected_universe=count,  # no live universe oracle; coverage gaps come from manifest quality
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
        ext=spec.ext,
    )


def emit_failed_manifest(minio: Minio, spec: FeedSpec, trade_date: date, reason: str) -> dict[str, Any]:
    """Writes a CT-003 FAILED manifest with no Bronze object, per OR-02 on_final_failure."""
    manifest = build_manifest(
        ingest_run_id=str(ULID()),
        source=spec.source,
        dataset=spec.dataset,
        trade_date=trade_date,
        source_version=spec.source_version,
        payloads=[],
        record_count=0,
        expected_universe=0,
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
        status="FAILED",
        notes=reason,
    )
    mkey = paths.manifest_key(spec.source, spec.dataset, trade_date, manifest["ingest_run_id"])
    _put(minio, mkey, json.dumps(manifest, indent=2).encode(), "application/json")
    return manifest
