"""
the coverage gate. Landing leaves coverage open because it only holds the bytes,
so this measures it against the expected universe, stamps the manifest, and
decides whether the run promotes, degrades to PARTIAL, or blocks.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from minio import Minio
from prefect import task

from pipeline.bronze.feeds import FEEDS, UNIVERSE_PAIRS
from pipeline.bronze.ingest import client
from pipeline.bronze.manifest import coverage_evaluated
from pipeline.bronze.sweep import read_manifest
from pipeline.config import get_settings
from pipeline.quality.coverage import CoverageResult, measure
from pipeline.quality.revise import revise_manifest, transport_status, worst
from pipeline.quality.universe import UniverseUnavailable

from orchestration.calendar import previous_trading_day
from orchestration.results import PhaseResult

UNIVERSE_FEEDS = [name for name, spec in FEEDS.items() if spec.is_universe]

STALE_AUTHORITY = "same-day company-profile authority unavailable"


def coverage_status(
    coverage: float, floor: float, hard_min: float, *, degraded: bool, stale: bool
) -> str:
    """The OR-06 decision table, with yesterday's membership never good enough to ship."""
    if stale or coverage < hard_min:
        return "FAILED"
    if coverage < floor:
        return "PARTIAL"
    return "PARTIAL" if degraded else "SUCCESS"


def quality_flags(result: CoverageResult, floor: float) -> list[str]:
    """The CT-008 flags this measurement earns; a stale full day is STALE, not a gap."""
    flags = []
    if result.stale_days > 0:
        flags.append("STALE")
    if result.coverage_ratio < floor:
        flags.append("COVERAGE_GAP")
    return flags


def result_degraded(result: CoverageResult) -> bool:
    """A named absent security is a gap, however small a share of the day it is."""
    return bool(result.missing_tickers)


def _side_feed_degraded(manifests: list[dict[str, Any]], trade_date: date) -> bool:
    """Whether any non-universe feed for the date landed short of clean."""
    return any(
        m["trade_date"] == trade_date.isoformat()
        and (m["source"], m["dataset"]) not in UNIVERSE_PAIRS
        and m["status"] in ("FAILED", "PARTIAL")
        for m in manifests
    )


def universe_transport_status(manifests: list[dict[str, Any]], trade_date: date) -> str:
    """What the day's universe landings themselves prove, read from their own numbers."""
    statuses = [
        transport_status(m)
        for m in manifests
        if m["trade_date"] == trade_date.isoformat()
        and (m["source"], m["dataset"]) in UNIVERSE_PAIRS
    ]
    return worst(*statuses) if statuses else "SUCCESS"


def fetch_gave_up(manifest: dict[str, Any]) -> bool:
    """A landing that wrote no object at all, as opposed to one this gate failed."""
    return manifest["status"] == "FAILED" and not manifest.get("object_count")


def failed_universe_feeds(manifests: list[dict[str, Any]], trade_date: date) -> list[str]:
    """Universe feeds whose fetch gave up, whatever older bytes still sit in Bronze."""
    return sorted(
        f"{m['source']}/{m['dataset']}"
        for m in manifests
        if m["trade_date"] == trade_date.isoformat()
        and (m["source"], m["dataset"]) in UNIVERSE_PAIRS
        and fetch_gave_up(m)
    )


def awaiting_coverage(manifest: dict[str, Any]) -> bool:
    """A universe feed that landed but has not reached the gate yet, so not a degrade."""
    pair = (manifest["source"], manifest["dataset"])
    return pair in UNIVERSE_PAIRS and not coverage_evaluated(manifest)


def universe_manifests(minio: Minio, trade_date: date) -> list[dict[str, Any]]:
    """Whatever manifest each universe feed currently has for the date, straight from Bronze."""
    found = []
    for name in UNIVERSE_FEEDS:
        spec = FEEDS[name]
        manifest = read_manifest(minio, spec.source, spec.dataset, trade_date, spec.source_version)
        if manifest is not None:
            found.append(manifest)
    return found


def blocked(reason: str) -> PhaseResult:
    """A gate that could not measure anything promotes nothing."""
    gate: dict[str, Any] = {
        "coverage_ratio": None,
        "promotion_ok": False,
        "missing_tickers": [],
        "reason": reason,
    }
    return PhaseResult(status="FAILED", payload=False, notes=reason, gate=gate)


def evaluate_coverage(
    manifests: list[dict[str, Any]],
    results: dict[str, CoverageResult],
    trade_date: date,
    floor: float,
    hard_min: float,
) -> PhaseResult:
    """Turns measured coverage plus the day's side feeds into the promotion decision."""
    if not results:
        return blocked("no universe feed landed for the date")

    worst_result = min(results.values(), key=lambda r: r.coverage_ratio)
    missing = sorted({t for r in results.values() for t in r.missing_tickers})
    stale = max(r.stale_days for r in results.values())
    degraded = _side_feed_degraded(manifests, trade_date) or any(
        result_degraded(r) for r in results.values()
    )

    # the same worst-wins rule the manifest uses, so the two can never disagree
    transport = universe_transport_status(manifests, trade_date)
    status = worst(
        transport,
        coverage_status(
            worst_result.coverage_ratio, floor, hard_min, degraded=degraded, stale=bool(stale)
        ),
    )
    promotion_ok = status != "FAILED"
    gate: dict[str, Any] = {
        "coverage_ratio": worst_result.coverage_ratio,
        "coverage_floor": floor,
        "coverage_hard_min": hard_min,
        "promotion_ok": promotion_ok,
        "missing_tickers": missing,
        "expected_universe": worst_result.expected_universe,
        "observed_universe": worst_result.observed_universe,
        "universe_as_of": worst_result.as_of.isoformat(),
        "universe_stale_days": stale,
    }
    verb = "promote" if promotion_ok else "blocked"
    notes = (
        f"coverage {worst_result.coverage_ratio} "
        f"({worst_result.observed_universe}/{worst_result.expected_universe}) "
        f"vs floor {floor} / hard_min {hard_min} -> {verb}"
    )
    if stale:
        gate["reason"] = STALE_AUTHORITY
        notes += (
            f"; {STALE_AUTHORITY}: newest is {worst_result.as_of.isoformat()}, "
            f"{stale}d before {trade_date.isoformat()}"
        )
    if transport != "SUCCESS":
        notes += "; upstream sent fewer rows than it declared"
    return PhaseResult(status=status, payload=promotion_ok, notes=notes, gate=gate)


def allowed_authority_dates(trade_date: date, calendar_seed: Path) -> frozenset[date]:
    """The only captures allowed to speak for a date: itself, or the session before it."""
    previous = previous_trading_day(trade_date, calendar_seed)
    return frozenset({trade_date} | ({previous} if previous else set()))


def measure_and_revise(
    trade_date: date,
    floor: float,
    hard_min: float,
    min_securities: int,
    calendar_seed: Path,
) -> dict[str, CoverageResult]:
    """Measures every universe feed for the date and stamps each one's manifest."""
    settings = get_settings()
    minio = client(settings)
    allowed_as_of = allowed_authority_dates(trade_date, calendar_seed)
    results: dict[str, CoverageResult] = {}
    for name in UNIVERSE_FEEDS:
        spec = FEEDS[name]
        result = measure(
            minio, spec, trade_date, allowed_as_of=allowed_as_of, min_securities=min_securities
        )
        status = coverage_status(
            result.coverage_ratio,
            floor,
            hard_min,
            degraded=result_degraded(result),
            stale=result.stale_days > 0,
        )
        revise_manifest(minio, spec, trade_date, result, status, quality_flags(result, floor))
        results[name] = result
    return results


def run_coverage_gate(
    manifests: list[dict[str, Any]],
    trade_date: date,
    floor: float,
    hard_min: float,
    min_securities: int,
    calendar_seed: Path,
) -> PhaseResult:
    """The one promotion decision, shared by the daily flow and the hourly resweep."""
    gave_up = failed_universe_feeds(manifests, trade_date)
    if gave_up:
        return blocked(f"universe feed never landed: {', '.join(gave_up)}")
    try:
        results = measure_and_revise(
            trade_date, floor, hard_min, min_securities, calendar_seed
        )
    except UniverseUnavailable as exc:
        return blocked(f"expected universe unavailable: {exc}")
    return evaluate_coverage(manifests, results, trade_date, floor, hard_min)


@task(name="coverage_gate")
def coverage_gate(
    manifests: list[dict[str, Any]],
    trade_date: date,
    floor: float,
    hard_min: float,
    min_securities: int,
    calendar_seed: Path,
) -> PhaseResult:
    return run_coverage_gate(manifests, trade_date, floor, hard_min, min_securities, calendar_seed)
