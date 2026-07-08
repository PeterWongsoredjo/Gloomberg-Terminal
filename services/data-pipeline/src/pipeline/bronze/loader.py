"""Loads fixture payloads into MinIO Bronze via the shared land primitive.

Fixtures mirror what live ingest lands. Each partition directory looks like
`{source}/{dataset}/ingest_date=YYYY-MM-DD/source_version=vN/` and holds `part-*.json`
raw payloads plus a `_meta.json` describing the manifest inputs (record_count,
expected_universe, missing_tickers). A deterministic run id makes a re-load replace, not pile up.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from minio import Minio

from pipeline.bronze.ingest import client, land_payloads
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.config import Settings, get_settings

META_FILE = "_meta.json"


def _parse_partition(part_dir: Path, kind_root: Path) -> tuple[str, str, date, str]:
    """Pulls source, dataset, trade_date, and source_version out of the Hive path."""
    rel = part_dir.relative_to(kind_root).parts
    ingest = next(p for p in rel if p.startswith("ingest_date="))
    version = next(p for p in rel if p.startswith("source_version="))
    idx = rel.index(ingest)
    source, dataset = rel[idx - 2], rel[idx - 1]
    trade_date = date.fromisoformat(ingest.removeprefix("ingest_date="))
    source_version = version.removeprefix("source_version=")
    return source, dataset, trade_date, source_version


def load_partition(minio: Minio, part_dir: Path, kind_root: Path) -> dict[str, Any]:
    """Loads one partition's payloads + manifest into Bronze; returns the manifest."""
    source, dataset, trade_date, source_version = _parse_partition(part_dir, kind_root)
    meta = json.loads((part_dir / META_FILE).read_text(encoding="utf-8"))
    run_id = deterministic_run_id(idempotency_key(source, dataset, trade_date, source_version))
    payloads = [part.read_bytes() for part in sorted(part_dir.glob("part-*.json"))]

    return land_payloads(
        minio,
        source=source,
        dataset=dataset,
        trade_date=trade_date,
        source_version=source_version,
        ingest_run_id=run_id,
        payloads=payloads,
        record_count=int(meta["record_count"]),
        expected_universe=int(meta.get("expected_universe", meta["record_count"])),
        missing_tickers=list(meta.get("missing_tickers", [])),
        requested_at=datetime.now(timezone.utc),
    )


def load_fixtures(fixtures_root: Path, settings: Settings) -> list[dict[str, Any]]:
    """Loads every partition under a fixtures kind directory into Bronze."""
    minio = client(settings)
    manifests: list[dict[str, Any]] = []
    for meta_path in sorted(fixtures_root.rglob(META_FILE)):
        manifests.append(load_partition(minio, meta_path.parent, fixtures_root))
    return manifests


def main() -> None:
    """CLI: load one or more fixture kind roots (default: frozen + curated + adversarial)."""
    settings = get_settings()
    base = Path(__file__).resolve().parents[3] / "fixtures"
    roots = [Path(a) for a in sys.argv[1:]] or [
        base / "frozen",
        base / "curated",
        base / "adversarial",
    ]
    for root in roots:
        if not root.exists():
            print(f"skip (missing): {root}")
            continue
        for manifest in load_fixtures(root, settings):
            q = manifest["quality"]
            print(
                f"loaded {manifest['source']}/{manifest['dataset']} "
                f"{manifest['trade_date']} {manifest['source_version']} "
                f"status={manifest['status']} records={manifest['record_count']} "
                f"coverage={q['coverage_ratio']}"
            )


if __name__ == "__main__":
    main()
