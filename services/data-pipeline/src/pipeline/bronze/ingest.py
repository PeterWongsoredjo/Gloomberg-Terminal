"""
Connects to the internet, fetches data, and lands it in the MinIO Bronze layer 
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
from pipeline.bronze.manifest import build_manifest, deterministic_run_id, idempotency_key
from pipeline.config import BRONZE_BUCKET, Settings


class FetchError(RuntimeError):
    """Upstream fetch failed, status_code is None for a connect/read timeout."""

    def __init__(self, status_code: int | None, message: str, *, via_proxy: bool = False) -> None:
        self.status_code = status_code
        self.via_proxy = via_proxy
        super().__init__(message)


def client(settings: Settings) -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_key,
        secret_key=settings.minio_secret,
        secure=settings.minio_secure,
    )


def _put(minio: Minio, key: str, data: bytes, content_type: str) -> None:
    minio.put_object(
        BRONZE_BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def fetch(url: str, *, proxy: str | None = None) -> bytes:
    """Fetches a Cloudflare-protected endpoint with a browser fingerprint, raising typed."""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = requests.get(url, impersonate="chrome", timeout=30, proxies=proxies)
    except Exception as exc:  # curl_cffi network errors are connect/read timeouts
        raise FetchError(None, f"{url} -> {exc}", via_proxy=proxy is not None) from exc
    if response.status_code != 200:
        raise FetchError(
            response.status_code, f"{url} -> HTTP {response.status_code}", via_proxy=proxy is not None
        )
    content: bytes = response.content
    return content


def record_count(raw: bytes, ext: str) -> int:
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


def run_id_for(spec: FeedSpec, trade_date: date) -> str:
    """Fresh id for accumulating feeds, deterministic id so EOD re-runs replace."""
    if spec.accumulates:
        return str(ULID())
    return deterministic_run_id(
        idempotency_key(spec.source, spec.dataset, trade_date, spec.source_version)
    )


def fetch_and_land(
    minio: Minio, spec: FeedSpec, trade_date: date, *, proxy: str | None = None
) -> dict[str, Any]:
    """Fetches one live feed and lands it in Bronze, raises FetchError on upstream failure."""
    url = spec.url.format(ymd=trade_date.strftime("%Y%m%d")) if spec.date_scoped else spec.url
    raw = fetch(url, proxy=proxy if spec.needs_proxy else None)
    count = record_count(raw, spec.ext)
    return land_payloads(
        minio,
        source=spec.source,
        dataset=spec.dataset,
        trade_date=trade_date,
        source_version=spec.source_version,
        ingest_run_id=run_id_for(spec, trade_date),
        payloads=[raw],
        record_count=count,
        expected_universe=count,  # no live universe oracle; coverage gaps come from manifest quality
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
        ext=spec.ext,
    )


def emit_failed_manifest(minio: Minio, spec: FeedSpec, trade_date: date, reason: str) -> dict[str, Any]:
    manifest = build_manifest(
        ingest_run_id=run_id_for(spec, trade_date),
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
