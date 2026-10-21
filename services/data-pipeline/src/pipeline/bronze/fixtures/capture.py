from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.bronze.core.feeds import FEEDS, EOD_FEEDS, FeedSpec
from pipeline.bronze.core.http import fetch, url_for
from pipeline.bronze.core.policy import landing_date, record_count
from pipeline.clock import now_utc

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "frozen"


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
        # a current-state feed only speaks for its pull day
        _write(spec, landing_date(spec, trade_date, captured_at), raw)
        print(f"captured {name}: {len(raw)} bytes, {record_count(raw, spec.ext)} records")


def main() -> None:
    import sys

    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    capture(trade_date)


if __name__ == "__main__":
    main()
