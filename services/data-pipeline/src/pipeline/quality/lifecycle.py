"""
Listing and delisting events, so the universe moves with the market between snapshots.

IDX publishes these in the share-count action feed, each row carrying its own effective
date. That makes a later capture safe to read: the log only grows, and every row says
when it took effect. dbt already maps the same rows to the CT-007 event types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from minio import Minio

from pipeline.bronze.ingest import read_object
from pipeline.config import BRONZE_BUCKET
from pipeline.reference.securities import TICKER_RE

ACTIONS_PREFIX = "corporate_actions/issued_history/"

# a full delisting; partialDelisting leaves the security trading, so it never removes one
DELIST_ACTIONS = frozenset({"delist"})
LISTING_ACTIONS = frozenset({"ipo", "CompanyListing"})


@dataclass(frozen=True)
class LifecycleEvent:
    """One security starting or stopping being listed, on the date IDX gave it."""

    ticker: str
    effective: date
    is_delisting: bool


def _effective(row: dict[str, Any]) -> date | None:
    raw = str(row.get("TanggalPencatatan") or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_events(raw: bytes) -> list[LifecycleEvent]:
    """Every listing and delisting the action feed records, undated rows dropped."""
    payload: Any = json.loads(raw)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("JenisTindakan") or "").strip()
        if action not in DELIST_ACTIONS | LISTING_ACTIONS:
            continue
        ticker = str(row.get("KodeEmiten") or "").strip().upper()
        effective = _effective(row)
        if not TICKER_RE.fullmatch(ticker) or effective is None:
            continue
        events.append(LifecycleEvent(ticker, effective, action in DELIST_ACTIONS))
    return events


def delisted_through(events: list[LifecycleEvent], through: date) -> set[str]:
    """Securities whose delisting had already taken effect by that date."""
    return {e.ticker for e in events if e.is_delisting and e.effective <= through}


def listed_between(events: list[LifecycleEvent], after: date, through: date) -> set[str]:
    """Securities that started trading in the gap a stale snapshot cannot know about."""
    return {e.ticker for e in events if not e.is_delisting and after < e.effective <= through}


def newest_events(minio: Minio) -> list[LifecycleEvent]:
    """Reads the freshest action feed landed, empty when none ever landed."""
    keys = [
        obj.object_name
        for obj in minio.list_objects(BRONZE_BUCKET, prefix=ACTIONS_PREFIX, recursive=True)
        if obj.object_name and obj.object_name.endswith(".zst")
    ]
    if not keys:
        return []
    return parse_events(read_object(minio, max(keys)))
