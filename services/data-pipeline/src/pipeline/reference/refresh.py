"""
Rebuilds the registry from the newest IDX company profiles landed in Bronze.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import zstandard
from minio import Minio

from pipeline.config import BRONZE_BUCKET
from pipeline.reference.securities import Security, load_baseline, securities_from_profiles
from pipeline.reference.store import write

_PREFIX = "company_profile/profiles/"

MIN_PLAUSIBLE_SECURITIES = 500


@dataclass(frozen=True)
class RefreshOutcome:
    """What the registry refresh actually landed, and whether it had to fall back."""

    count: int
    source: str
    notes: str

    @property
    def degraded(self) -> bool:
        return self.source != "bronze"


def _newest_payload(minio: Minio) -> bytes | None:
    """The most recent company-profile payload in Bronze, decompressed."""
    keys = [
        obj.object_name
        for obj in minio.list_objects(BRONZE_BUCKET, prefix=_PREFIX, recursive=True)
        if obj.object_name and obj.object_name.endswith(".zst")
    ]
    if not keys:
        return None
    response = minio.get_object(BRONZE_BUCKET, max(keys))
    try:
        return zstandard.ZstdDecompressor().decompress(response.read())
    finally:
        response.close()
        response.release_conn()


def _from_bronze(minio: Minio) -> tuple[list[Security], str]:
    try:
        raw = _newest_payload(minio)
    except Exception as exc:  # object store trouble must not empty the registry
        return [], f"bronze unreadable: {exc}"
    if raw is None:
        return [], "no company_profile payload in bronze"
    try:
        securities = securities_from_profiles(json.loads(raw))
    except ValueError as exc:
        return [], f"company_profile payload unparseable: {exc}"
    if len(securities) < MIN_PLAUSIBLE_SECURITIES:
        return [], f"only {len(securities)} securities in payload, below the plausible floor"
    return securities, ""


def refresh(minio: Minio, dsn: str) -> RefreshOutcome:
    """Rebuilds the registry, falling back to the committed baseline rather than emptying it."""
    securities, problem = _from_bronze(minio)
    if problem:
        securities = load_baseline()
        write(dsn, securities, "baseline")
        return RefreshOutcome(len(securities), "baseline", problem)
    write(dsn, securities, "bronze")
    return RefreshOutcome(len(securities), "bronze", "refreshed from the newest bronze payload")
