"""Publishes the built Gold to the read-only snapshot the serving layer opens.

dbt writes the build database; this copies only the materialized Gold tables (dim/fct/agg)
into a fresh, view-free snapshot and swaps it into place atomically. Serving never sees a
half-built Gold because the swap is a single os.replace onto the published path (ADR-001).
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from pipeline.config import Settings, get_settings

GOLD_PREFIXES = ("dim_", "fct_", "agg_")


def _gold_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Names of the materialized Gold tables to publish, in the build database."""
    rows = con.execute(
        """
        select table_name
        from duckdb_tables()
        where database_name = 'build' and schema_name = 'main'
        order by table_name
        """
    ).fetchall()
    return [t for (t,) in rows if t.startswith(GOLD_PREFIXES)]


def publish(settings: Settings) -> list[str]:
    """Builds a fresh snapshot from the build DB and atomically swaps it into place."""
    src = settings.build_duckdb
    dst = settings.published_gold
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        tmp.unlink()

    con = duckdb.connect(str(tmp))
    try:
        con.execute(f"ATTACH '{src.as_posix()}' AS build (READ_ONLY)")
        tables = _gold_tables(con)
        for table in tables:
            con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM build.main."{table}"')
    finally:
        con.close()

    os.replace(tmp, dst)  # atomic on the same volume; readers see whole-old or whole-new
    return tables


def main() -> None:
    """CLI: publish the current build to the serving snapshot."""
    settings = get_settings()
    published = publish(settings)
    print(f"published {len(published)} Gold tables -> {Path(settings.published_gold)}")
    for table in published:
        print(f"  {table}")


if __name__ == "__main__":
    main()
