"""
Captures frozen-real IDX payloads as Bronze fixtures via a TLS-spoofing client.

The IDX and KSEI sites sit behind Cloudflare, a plain client gets 403. The shared `fetch`
helper presents a real browser fingerprint and gets through. Run once to refresh the
committed JSON fixtures under `fixtures/frozen/`. Endpoints live in `pipeline.bronze.feeds`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.bronze.feeds import FEEDS, EOD_FEEDS, FeedSpec
from pipeline.bronze.ingest import fetch, landing_date, record_count, url_for
from pipeline.clock import now_utc

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "frozen"


def _write(spec: FeedSpec, trade_date: date, raw: bytes) -> None:
    """Persists a raw payload plus its _meta.json under the Hive fixture path."""
    part_dir = (
        FIXTURES / spec.source / spec.dataset
        / f"ingest_date={trade_date.isoformat()}" / f"source_version={spec.source_version}"
    )
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / f"part-0000.{spec.ext}").write_bytes(raw)
    meta = {"record_count": record_count(raw, spec.ext)}
    (part_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def capture(trade_date: date) -> None:
    """Captures every JSON EOD feed for one trade_date into the frozen fixtures tree."""
    for name in EOD_FEEDS:
        spec = FEEDS[name]
        captured_at = now_utc()
        raw = fetch(url_for(spec, trade_date))
        # a current-state feed can only speak for the day it was pulled
        _write(spec, landing_date(spec, trade_date, captured_at), raw)
        print(f"captured {name}: {len(raw)} bytes, {record_count(raw, spec.ext)} records")


def main() -> None:
    """CLI: capture a given YYYY-MM-DD trade_date's feeds."""
    import sys

    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    capture(trade_date)


if __name__ == "__main__":
    main()
