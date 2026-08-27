"""Builds the ingestion run manifest - the idempotency + coverage anchor."""

from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime, timezone
from typing import Any


def deterministic_run_id(idempotency_key: str) -> str:
    """Stable 26-char run id from an idempotency key, so a re-run replaces, not piles up."""
    digest = hashlib.sha256(idempotency_key.encode()).digest()
    return base64.b32encode(digest).decode().rstrip("=")[:26]


def content_sha256(payloads: list[bytes]) -> str:
    """Hash of the concatenated sorted payloads, so a re-ingest is detectable."""
    digest = hashlib.sha256()
    for payload in sorted(payloads):
        digest.update(payload)
    return digest.hexdigest()


def idempotency_key(source: str, dataset: str, trade_date: date, source_version: str) -> str:
    """The key that makes a same-scope re-run replace, not duplicate."""
    return f"{source}:{dataset}:{trade_date.isoformat()}:{source_version}"


def quality_block(
    expected_universe: int | None,
    missing_tickers: list[str] | None,
    dq_flags: list[str] | None = None,
) -> dict[str, Any]:
    """The coverage numbers, all null when nobody has actually compared anything yet."""
    flags = list(dq_flags or [])
    if not expected_universe:
        return {
            "expected_universe": None,
            "observed_universe": None,
            "missing_tickers": [],
            "coverage_ratio": None,
            "dq_flags": flags,
        }
    missing = list(missing_tickers or [])
    observed = expected_universe - len(missing)
    return {
        "expected_universe": expected_universe,
        "observed_universe": observed,
        "missing_tickers": missing,
        "coverage_ratio": round(observed / expected_universe, 4),
        "dq_flags": flags,
    }


def coverage_evaluated(manifest: dict[str, Any]) -> bool:
    """Whether anything ever measured this run's coverage, rather than leaving it open."""
    quality: dict[str, Any] = manifest.get("quality") or {}
    return quality.get("coverage_ratio") is not None


def build_manifest(
    *,
    ingest_run_id: str,
    source: str,
    dataset: str,
    trade_date: date,
    source_version: str,
    payloads: list[bytes],
    record_count: int,
    requested_at: datetime,
    expected_universe: int | None = None,
    missing_tickers: list[str] | None = None,
    declared_record_total: int | None = None,
    status: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Assembles a full manifest for one Bronze ingestion run."""
    quality = quality_block(expected_universe, missing_tickers)
    resolved_status = status or ("SUCCESS" if not quality["missing_tickers"] else "PARTIAL")
    bytes_written = sum(len(p) for p in payloads)
    return {
        "schema_version": "1.1.0",
        "ingest_run_id": ingest_run_id,
        "source": source,
        "dataset": dataset,
        "trade_date": trade_date.isoformat(),
        "source_version": source_version,
        "requested_at": iso_utc(requested_at),
        "completed_at": iso_utc(datetime.now(timezone.utc)),
        "status": resolved_status,
        "object_count": len(payloads),
        "record_count": record_count,
        "bytes_written": bytes_written,
        "content_sha256": content_sha256(payloads),
        "idempotency_key": idempotency_key(source, dataset, trade_date, source_version),
        "upstream": {"declared_record_total": declared_record_total},
        "quality": quality,
        "notes": notes,
    }


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
