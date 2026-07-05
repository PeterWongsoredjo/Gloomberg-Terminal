"""Builds the CT-003 ingestion run manifest — the idempotency + coverage anchor."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any


def content_sha256(payloads: list[bytes]) -> str:
    """Hash of the concatenated sorted payloads, so a re-ingest is detectable."""
    digest = hashlib.sha256()
    for payload in sorted(payloads):
        digest.update(payload)
    return digest.hexdigest()


def idempotency_key(source: str, dataset: str, trade_date: date, source_version: str) -> str:
    """The CT-003 key that makes a same-scope re-run replace, not duplicate."""
    return f"{source}:{dataset}:{trade_date.isoformat()}:{source_version}"


def build_manifest(
    *,
    ingest_run_id: str,
    source: str,
    dataset: str,
    trade_date: date,
    source_version: str,
    payloads: list[bytes],
    record_count: int,
    expected_universe: int,
    missing_tickers: list[str],
    requested_at: datetime,
    status: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Assembles a full CT-003 manifest for one Bronze ingestion run."""
    observed = expected_universe - len(missing_tickers)
    coverage_ratio = round(observed / expected_universe, 4) if expected_universe else 1.0
    resolved_status = status or ("SUCCESS" if not missing_tickers else "PARTIAL")
    bytes_written = sum(len(p) for p in payloads)
    return {
        "schema_version": "1.0.0",
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
        "quality": {
            "expected_universe": expected_universe,
            "observed_universe": observed,
            "missing_tickers": missing_tickers,
            "coverage_ratio": coverage_ratio,
        },
        "notes": notes,
    }


def iso_utc(moment: datetime) -> str:
    """UTC RFC-3339 with a trailing Z, per CT-001."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
