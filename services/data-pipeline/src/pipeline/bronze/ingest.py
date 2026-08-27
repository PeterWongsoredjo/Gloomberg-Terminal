"""
Connects to the internet, fetches data, and lands it in the MinIO Bronze layer 
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta
from typing import Any

import zstandard
from curl_cffi import requests
from minio import Minio
from ulid import ULID

from pipeline.bronze import paths
from pipeline.bronze.feeds import FeedSpec
from pipeline.bronze.manifest import build_manifest, deterministic_run_id, idempotency_key
from pipeline.clock import now_utc, wib_date
from pipeline.config import BRONZE_BUCKET, Settings


class FetchError(RuntimeError):
    """Upstream fetch failed, status_code is None for a connect/read timeout."""

    def __init__(self, status_code: int | None, message: str, *, via_proxy: bool = False) -> None:
        self.status_code = status_code
        self.via_proxy = via_proxy
        super().__init__(message)


def is_transient_fetch(exc: FetchError) -> bool:
    """A fetch is worth retrying on a timeout, a 429, any 5xx, or a proxied 403."""
    if exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500:
        return True
    # a proxied 403 is a bad ip draw, the next attempt gets a fresh one
    return exc.status_code == 403 and exc.via_proxy


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


def read_object(minio: Minio, key: str) -> bytes:
    """Reads one Bronze object back and decompresses it."""
    response = minio.get_object(BRONZE_BUCKET, key)
    try:
        return bytes(zstandard.ZstdDecompressor().decompress(response.read()))
    finally:
        response.close()
        response.release_conn()


def fetch(url: str, *, proxy: str | None = None) -> bytes:
    """Fetches a Cloudflare-protected endpoint with a browser fingerprint, raising typed."""
    try:
        response = requests.get(url, impersonate="chrome", timeout=30, proxy=proxy)
    except Exception as exc:  # curl_cffi network errors are connect/read timeouts
        raise FetchError(None, f"{url} -> {exc}", via_proxy=proxy is not None) from exc
    if response.status_code != 200:
        raise FetchError(
            response.status_code, f"{url} -> HTTP {response.status_code}", via_proxy=proxy is not None
        )
    content: bytes = response.content
    return content


def url_for(spec: FeedSpec, trade_date: date) -> str:
    """The feed's url for one date, with its date window filled in."""
    if not spec.date_scoped:
        return spec.url
    window_start = trade_date - timedelta(days=spec.lookback_days)
    return spec.url.format(
        ymd=trade_date.strftime("%Y%m%d"), ymd_from=window_start.strftime("%Y%m%d")
    )


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


def upstream_record_total(raw: bytes, ext: str) -> int | None:
    """How many rows the upstream itself claims it has, None when it never says."""
    if ext == "xml":
        return None
    parsed: Any = json.loads(raw)
    if not isinstance(parsed, dict):
        return None
    total = parsed.get("recordsTotal")
    return total if isinstance(total, int) else None


def write_manifest(minio: Minio, manifest: dict[str, Any]) -> None:
    """Puts a manifest at its own key, so a revision lands exactly where the run did."""
    key = paths.manifest_key(
        manifest["source"],
        manifest["dataset"],
        date.fromisoformat(manifest["trade_date"]),
        manifest["ingest_run_id"],
    )
    _put(minio, key, json.dumps(manifest, indent=2).encode(), "application/json")


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
        declared_record_total=declared_record_total,
        requested_at=requested_at,
        status=status,
        notes=notes,
    )
    write_manifest(minio, manifest)
    return manifest


def landing_date(spec: FeedSpec, trade_date: date, captured_at: datetime) -> date:
    """The date a payload files under: what it asked for, or when it was taken."""
    if spec.current_state:
        return wib_date(captured_at)
    return trade_date


def run_id_for(spec: FeedSpec, landed_on: date) -> str:
    """Fresh id for accumulating feeds, deterministic id so EOD re-runs replace."""
    if spec.accumulates:
        return str(ULID())
    return deterministic_run_id(
        idempotency_key(spec.source, spec.dataset, landed_on, spec.source_version)
    )


def landing_verdict(spec: FeedSpec, count: int, declared: int | None) -> tuple[str, str]:
    """What raw landing alone can honestly say about the payload it just wrote."""
    if declared is not None and count != declared:
        return "PARTIAL", f"upstream declared {declared} rows, {count} arrived"
    if spec.is_universe:
        return "PARTIAL", "coverage not evaluated yet"
    return "SUCCESS", ""


def fetch_and_land(
    minio: Minio, spec: FeedSpec, trade_date: date, *, proxy: str | None = None
) -> dict[str, Any]:
    """Fetches one live feed and lands it in Bronze, raises FetchError on upstream failure."""
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
