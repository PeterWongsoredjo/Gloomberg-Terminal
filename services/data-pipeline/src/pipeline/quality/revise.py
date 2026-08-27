"""
Writes a measured coverage result back onto the run's own manifest.

Landing wrote that manifest with the coverage left open, because landing has only
the bytes. Revising the same key keeps one manifest per run, so dbt lineage, the
resweep and the telemetry projection all keep reading exactly one thing.

Two separate questions get answered here, and neither may erase the other: did the
upstream send everything it said it would, and did every expected security arrive.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from minio import Minio

from pipeline.bronze.feeds import FeedSpec
from pipeline.bronze.ingest import write_manifest
from pipeline.bronze.manifest import quality_block
from pipeline.bronze.sweep import read_manifest
from pipeline.quality.coverage import CoverageResult

# worst wins, so neither verdict can be talked up by the other
SEVERITY = {"SUCCESS": 0, "PARTIAL": 1, "FAILED": 2}


def transport_status(manifest: dict[str, Any]) -> str:
    """What the landing itself proved, read from numbers rather than from its prose."""
    if not manifest.get("object_count"):
        return "FAILED"  # the fetch wrote nothing at all
    declared = (manifest.get("upstream") or {}).get("declared_record_total")
    if declared is not None and manifest.get("record_count") != declared:
        return "PARTIAL"
    return "SUCCESS"


def transport_note(manifest: dict[str, Any]) -> str:
    """The landing's own evidence, rebuilt from the manifest's numbers."""
    if not manifest.get("object_count"):
        return "no object landed"
    declared = (manifest.get("upstream") or {}).get("declared_record_total")
    count = manifest.get("record_count")
    if declared is not None and count != declared:
        return f"upstream declared {declared} rows, {count} arrived"
    return ""


def worst(*statuses: str) -> str:
    """The most severe of several verdicts on the same run."""
    return max(statuses, key=lambda s: SEVERITY.get(s, 0))


def coverage_notes(result: CoverageResult) -> str:
    """Plain-language summary of what the comparison found."""
    note = (
        f"coverage {result.observed_universe} of {result.expected_universe} expected "
        f"securities, universe as of {result.as_of.isoformat()}"
    )
    if result.stale_days:
        note += f" ({result.stale_days}d stale)"
    if result.missing_tickers:
        note += f"; missing {', '.join(result.missing_tickers[:20])}"
    return note


def revised(
    manifest: dict[str, Any],
    result: CoverageResult,
    coverage_status: str,
    dq_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Folds the measured coverage in beside what landing already proved."""
    notes = [transport_note(manifest), coverage_notes(result)]
    return {
        **manifest,
        "status": worst(transport_status(manifest), coverage_status),
        "quality": quality_block(result.expected_universe, result.missing_tickers, dq_flags),
        "notes": "; ".join(note for note in notes if note),
    }


def revise_manifest(
    minio: Minio,
    spec: FeedSpec,
    trade_date: date,
    result: CoverageResult,
    coverage_status: str,
    dq_flags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Stamps the measured coverage onto the landed manifest, None when none landed."""
    manifest = read_manifest(minio, spec.source, spec.dataset, trade_date, spec.source_version)
    if manifest is None:
        return None
    updated = revised(manifest, result, coverage_status, dq_flags)
    write_manifest(minio, updated)
    return updated
