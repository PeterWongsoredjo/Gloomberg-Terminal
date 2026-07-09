"""
syncs aggregate tables in duckdb to postgres
"""

from __future__ import annotations

import duckdb

from pipeline.config import Settings, get_settings


def _agg_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        """
        select table_name
        from duckdb_tables()
        where database_name = 'gold' and schema_name = 'main' and table_name like 'agg_%'
        order by table_name
        """
    ).fetchall()
    return [t for (t,) in rows]


def sync(settings: Settings) -> list[str]:
    """Replaces every agg_* table in Postgres from the built Gold, transactionally."""
    con = duckdb.connect()
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{settings.build_duckdb.as_posix()}' AS gold (READ_ONLY)")
        con.execute(f"ATTACH '{settings.postgres_dsn}' AS pg (TYPE postgres)")
        tables = _agg_tables(con)

        con.execute("BEGIN TRANSACTION")
        for table in tables:
            con.execute(
                f'CREATE TABLE IF NOT EXISTS pg.public."{table}" '
                f'AS SELECT * FROM gold.main."{table}" WHERE false'
            )
            con.execute(f'DELETE FROM pg.public."{table}"')
            con.execute(f'INSERT INTO pg.public."{table}" SELECT * FROM gold.main."{table}"')
        con.execute("COMMIT")
        return tables
    finally:
        con.close()


def main() -> None:
    settings = get_settings()
    synced = sync(settings)
    print(f"synced {len(synced)} projections -> postgres {settings.postgres_host}:{settings.postgres_port}")
    for table in synced:
        print(f"  {table}")


if __name__ == "__main__":
    main()
