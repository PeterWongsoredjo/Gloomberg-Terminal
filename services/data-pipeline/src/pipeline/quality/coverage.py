"""
Compares the expected security universe against what the day's trade payload landed.

Runs downstream of raw landing, because landing only ever holds the upstream bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from minio import Minio

from pipeline.bronze import paths
from pipeline.bronze.feeds import FeedSpec
from pipeline.bronze.ingest import read_object, run_id_for
from pipeline.config import BRONZE_BUCKET
from pipeline.quality.lifecycle import (
    LifecycleEvent,
    delisted_through,
    listed_between,
    newest_events,
)
from pipeline.quality.universe import ExpectedUniverse, UniverseUnavailable, expected_universe


@dataclass(frozen=True)
class CoverageResult:
    """One measured comparison: how much of the expected universe actually arrived."""

    expected_universe: int
    observed_universe: int
    missing_tickers: list[str]
    coverage_ratio: float
    as_of: date
    stale_days: int
    authority_key: str


def observed_tickers(raw: bytes) -> set[str]:
    """Every stock code present in a landed trade payload."""
    payload: Any = json.loads(raw)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return set()
    codes = (str(r.get("StockCode") or "").strip().upper() for r in rows if isinstance(r, dict))
    return {code for code in codes if code}


def evaluate(expected: ExpectedUniverse, observed: set[str]) -> CoverageResult:
    """The coverage numbers, counting only securities the day was supposed to carry."""
    present = expected.tickers & observed
    return CoverageResult(
        expected_universe=len(expected.tickers),
        observed_universe=len(present),
        missing_tickers=sorted(expected.tickers - observed),
        coverage_ratio=round(len(present) / len(expected.tickers), 4),
        as_of=expected.as_of,
        stale_days=expected.stale_days,
        authority_key=expected.authority_key,
    )


def landed_tickers(minio: Minio, spec: FeedSpec, trade_date: date) -> set[str]:
    """Reads back every part the day's trade run wrote, raising when it wrote none."""
    run_id = run_id_for(spec, trade_date)
    prefix = paths.partition_prefix(spec.source, spec.dataset, trade_date, spec.source_version)
    keys = [
        obj.object_name
        for obj in minio.list_objects(BRONZE_BUCKET, prefix=prefix, recursive=True)
        if obj.object_name and f"part-{run_id}-" in obj.object_name
    ]
    if not keys:
        raise UniverseUnavailable(
            f"no landed {spec.dataset} payload for {trade_date.isoformat()} to measure"
        )
    return {ticker for key in sorted(keys) for ticker in observed_tickers(read_object(minio, key))}


def apply_lifecycle(
    snapshot: ExpectedUniverse, events: list[LifecycleEvent], trade_date: date
) -> ExpectedUniverse:
    """Moves the snapshot forward to the date: listings in the gap in, delistings out."""
    joined = listed_between(events, snapshot.as_of, trade_date)
    left = delisted_through(events, trade_date)
    return replace(snapshot, tickers=frozenset((snapshot.tickers | joined) - left))


def measure(
    minio: Minio,
    spec: FeedSpec,
    trade_date: date,
    *,
    allowed_as_of: frozenset[date],
    min_securities: int,
) -> CoverageResult:
    """Measures one universe feed's coverage for a date, or fails closed."""
    snapshot = expected_universe(
        minio, trade_date, allowed_as_of=allowed_as_of, min_securities=min_securities
    )
    expected = apply_lifecycle(snapshot, newest_events(minio), trade_date)
    return evaluate(expected, landed_tickers(minio, spec, trade_date))
