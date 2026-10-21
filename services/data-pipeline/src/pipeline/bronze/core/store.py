from __future__ import annotations

import io
from datetime import date

import zstandard
from minio import Minio

from pipeline.config import BRONZE_BUCKET, Settings


def client(settings: Settings) -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_key,
        secret_key=settings.minio_secret,
        secure=settings.minio_secure,
    )


def put(minio: Minio, key: str, data: bytes, content_type: str) -> None:
    minio.put_object(
        BRONZE_BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def read_object(minio: Minio, key: str) -> bytes:
    response = minio.get_object(BRONZE_BUCKET, key)
    try:
        return bytes(zstandard.ZstdDecompressor().decompress(response.read()))
    finally:
        response.close()
        response.release_conn()


def ingest_dates(minio: Minio, prefix: str) -> set[date]:
    """Which days already have objects under a Bronze prefix."""
    found: set[date] = set()
    for obj in minio.list_objects(BRONZE_BUCKET, prefix=prefix, recursive=True):
        for part in (obj.object_name or "").split("/"):
            if not part.startswith("ingest_date="):
                continue
            try:
                found.add(date.fromisoformat(part.removeprefix("ingest_date=")))
            except ValueError:
                pass
    return found


def partition_prefix(source: str, dataset: str, trade_date: date, source_version: str) -> str:
    """Key prefix for one day's partition, so MinIO filters server-side."""
    return (
        f"{source}/{dataset}/"
        f"ingest_date={trade_date.isoformat()}/"
        f"source_version={source_version}/"
    )


def object_key(
    source: str,
    dataset: str,
    trade_date: date,
    source_version: str,
    ingest_run_id: str,
    seq: int,
    ext: str = "json",
) -> str:
    return (
        partition_prefix(source, dataset, trade_date, source_version)
        + f"part-{ingest_run_id}-{seq:04d}.{ext}.zst"
    )


def manifest_key(source: str, dataset: str, trade_date: date, ingest_run_id: str) -> str:
    return (
        f"_manifests/{source}/{dataset}/"
        f"ingest_date={trade_date.isoformat()}/{ingest_run_id}.json"
    )
