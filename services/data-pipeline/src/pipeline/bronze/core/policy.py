from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ulid import ULID

from pipeline.bronze.core.feeds import FeedSpec
from pipeline.bronze.core.manifest import deterministic_run_id, idempotency_key
from pipeline.clock import wib_date


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
