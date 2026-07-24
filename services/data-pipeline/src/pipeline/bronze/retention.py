"""
Prunes superseded Bronze manifests, the newest per scope always survives.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from pipeline.bronze.ingest import client
from pipeline.config import BRONZE_BUCKET, get_settings

MANIFESTS_PREFIX = "_manifests/"
DEFAULT_KEEP_DAYS = 90


def _parse_scope(key: str) -> tuple[str, date] | None:
    """Splits a manifest key into its scope prefix and ingest date."""
    parts = key.split("/")
    if len(parts) != 5 or parts[0] != "_manifests":
        return None
    if not parts[3].startswith("ingest_date="):
        return None
    try:
        ingest_date = date.fromisoformat(parts[3].removeprefix("ingest_date="))
    except ValueError:
        return None
    return "/".join(parts[:4]) + "/", ingest_date


def prune_candidates(
    objects: list[tuple[str, datetime]], today: date, keep_days: int
) -> list[str]:
    """Every manifest except the newest per scope, old ingest dates only."""
    cutoff = today - timedelta(days=keep_days)
    by_scope: dict[str, list[tuple[datetime, str]]] = {}
    for key, modified in objects:
        parsed = _parse_scope(key)
        if parsed is None:
            continue
        scope, ingest_date = parsed
        if ingest_date >= cutoff:
            continue
        by_scope.setdefault(scope, []).append((modified, key))
    doomed: list[str] = []
    for members in by_scope.values():
        members.sort()
        doomed.extend(key for _, key in members[:-1])
    return sorted(doomed)


def main() -> None:
    """CLI: list prune candidates, delete them only with the apply flag."""
    parser = argparse.ArgumentParser(description="prune superseded Bronze manifests")
    parser.add_argument("--apply", action="store_true", help="delete instead of dry run")
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    args = parser.parse_args()

    minio = client(get_settings())
    listing = [
        (obj.object_name, obj.last_modified)
        for obj in minio.list_objects(BRONZE_BUCKET, prefix=MANIFESTS_PREFIX, recursive=True)
        if obj.object_name is not None and obj.last_modified is not None
    ]
    doomed = prune_candidates(listing, date.today(), args.keep_days)
    verb = "delete" if args.apply else "would delete"
    for key in doomed:
        print(f"{verb} {key}")
        if args.apply:
            minio.remove_object(BRONZE_BUCKET, key)
    print(f"{len(doomed)} of {len(listing)} manifests {'deleted' if args.apply else 'selected'}")


if __name__ == "__main__":
    main()
