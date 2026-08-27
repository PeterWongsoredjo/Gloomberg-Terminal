"""
The expected security universe for one trade date.

Read from the IDX company-profile snapshot landed in Bronze. That endpoint takes no
date, so it can only ever describe the moment it was called: the partition label alone
proves nothing about when the bytes were true. Three things together make it authority
here - the partition, the capture stamp on its own manifest that has to agree with it,
and a caller-supplied window of dates the answer is allowed to come from.

The Postgres registry cannot answer this at all: it is upserted in place with no history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from minio import Minio

from pipeline.bronze import paths
from pipeline.bronze.ingest import read_object
from pipeline.bronze.sweep import read_manifest
from pipeline.clock import wib_date
from pipeline.config import BRONZE_BUCKET
from pipeline.reference.securities import TICKER_RE, profile_rows

PROFILE_SOURCE = "company_profile"
PROFILE_DATASET = "profiles"

# a real IDX day lists roughly a thousand equities; this is a second guard, never a
# completeness check - only the declared record total can speak to completeness
MIN_PLAUSIBLE_SECURITIES = 500


class UniverseUnavailable(RuntimeError):
    """No trustworthy expected universe for the date, so nothing may be promoted."""


@dataclass(frozen=True)
class ExpectedUniverse:
    """Who should have traded on a date, and which capture says so."""

    tickers: frozenset[str]
    as_of: date
    stale_days: int
    authority_key: str


def listing_date(row: dict[str, Any]) -> date | None:
    """When IDX says the security started trading, None when it does not say."""
    raw = str(row.get("TanggalPencatatan") or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def equity_tickers(payload: Any, as_of: date) -> set[str]:
    """Every listed equity in the snapshot that had already listed by that date."""
    tickers = set()
    for row in profile_rows(payload):
        if not row.get("EfekEmiten_Saham"):
            continue
        ticker = str(row.get("KodeEmiten") or "").strip().upper()
        if not TICKER_RE.fullmatch(ticker):
            continue
        listed = listing_date(row)
        if listed is not None and listed > as_of:
            continue  # not listed yet on the date being evaluated
        tickers.add(ticker)
    return tickers


def declared_total(payload: Any) -> int:
    """The row count the payload claims, refusing anything that is not a plain integer."""
    if not isinstance(payload, dict) or "recordsTotal" not in payload:
        raise UniverseUnavailable("company_profile payload declares no recordsTotal")
    total = payload["recordsTotal"]
    # bool is an int in python, and a flag is not a row count
    if isinstance(total, bool) or not isinstance(total, int):
        raise UniverseUnavailable(f"company_profile recordsTotal is not an integer: {total!r}")
    return total


def parse_authority(raw: bytes) -> Any:
    """Loads a profile payload, refusing any whose row count is not exactly as declared."""
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UniverseUnavailable(f"company_profile payload unparseable: {exc}") from exc
    declared = declared_total(payload)
    rows = profile_rows(payload)
    if len(rows) != declared:
        raise UniverseUnavailable(
            f"company_profile row count disagrees with its own total: "
            f"{len(rows)} rows, {declared} declared"
        )
    return payload


def _snapshot_key(minio: Minio, captured_on: date) -> str | None:
    """The profile object filed under one capture date, None when nothing is."""
    prefix = paths.partition_prefix(PROFILE_SOURCE, PROFILE_DATASET, captured_on, "v1")
    keys = [
        obj.object_name
        for obj in minio.list_objects(BRONZE_BUCKET, prefix=prefix, recursive=True)
        if obj.object_name and obj.object_name.endswith(".zst")
    ]
    return max(keys) if keys else None


def newest_snapshot(minio: Minio, allowed_as_of: frozenset[date]) -> tuple[str, date]:
    """The freshest profile snapshot from the dates the caller allows, or fails closed."""
    for captured_on in sorted(allowed_as_of, reverse=True):
        key = _snapshot_key(minio, captured_on)
        if key is not None:
            return key, captured_on
    allowed = ", ".join(d.isoformat() for d in sorted(allowed_as_of))
    raise UniverseUnavailable(f"no company_profile snapshot captured on {allowed or 'any allowed date'}")


def capture_date(manifest: dict[str, Any]) -> date:
    """The WIB day a landing actually pulled its bytes, from its own capture stamp."""
    stamped = str(manifest.get("requested_at") or "")
    try:
        moment = datetime.strptime(stamped, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise UniverseUnavailable(f"landing carries no readable capture time: {stamped!r}") from None
    return wib_date(moment)


def _assert_captured_when_filed(minio: Minio, key: str, filed_under: date) -> None:
    """A snapshot filed under a day it was not taken on is not evidence of that day."""
    manifest = read_manifest(minio, PROFILE_SOURCE, PROFILE_DATASET, filed_under)
    if manifest is None:
        raise UniverseUnavailable(f"company_profile snapshot {key} has no manifest to date it")
    captured_on = capture_date(manifest)
    if captured_on != filed_under:
        raise UniverseUnavailable(
            f"company_profile snapshot filed under {filed_under.isoformat()} "
            f"was captured {captured_on.isoformat()}"
        )


def expected_universe(
    minio: Minio,
    trade_date: date,
    *,
    allowed_as_of: frozenset[date],
    min_securities: int = MIN_PLAUSIBLE_SECURITIES,
) -> ExpectedUniverse:
    """The securities that should appear in the day's trade payload, or fails closed."""
    key, as_of = newest_snapshot(minio, allowed_as_of)
    _assert_captured_when_filed(minio, key, as_of)
    try:
        raw = read_object(minio, key)
    except Exception as exc:  # an unreadable authority is an absent authority
        raise UniverseUnavailable(f"company_profile snapshot {key} unreadable: {exc}") from exc

    tickers = equity_tickers(parse_authority(raw), trade_date)
    if len(tickers) < min_securities:
        raise UniverseUnavailable(
            f"only {len(tickers)} equities in {key}, below the plausible floor {min_securities}"
        )
    return ExpectedUniverse(
        tickers=frozenset(tickers),
        as_of=as_of,
        stale_days=(trade_date - as_of).days,
        authority_key=key,
    )
