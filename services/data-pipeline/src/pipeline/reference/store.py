"""
Keeps the IDX security registry in Postgres, where every service can reach it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from pipeline.reference.securities import Security, load_baseline

_DDL = """
create schema if not exists reference;

create table if not exists reference.idx_security (
    ticker text primary key,
    company_name text not null,
    aliases text[] not null default '{}',
    board text,
    is_current boolean not null default true,
    source text not null,
    refreshed_at timestamptz not null default now()
);

create index if not exists idx_security_current_idx on reference.idx_security (is_current);
"""

_UPSERT = """
insert into reference.idx_security
    (ticker, company_name, aliases, board, is_current, source, refreshed_at)
values (%s, %s, %s, %s, true, %s, %s)
on conflict (ticker) do update set
    company_name = excluded.company_name,
    aliases = excluded.aliases,
    board = excluded.board,
    is_current = true,
    source = excluded.source,
    refreshed_at = excluded.refreshed_at
"""

_RETIRE = """
update reference.idx_security
set is_current = false, refreshed_at = %s
where is_current and ticker <> all(%s)
"""

_SELECT_CURRENT = """
select ticker, company_name, board, aliases
from reference.idx_security
where is_current
order by ticker
"""

_MAX_REFRESHED_AT = "select max(refreshed_at) from reference.idx_security"


def setup(dsn: str) -> None:
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        conn.execute(_DDL)


def write(dsn: str, securities: list[Security], source: str) -> int:
    """Upserts the registry and retires anything the payload dropped."""
    stamp = datetime.now(timezone.utc)
    tickers = [s.ticker for s in securities]
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(_DDL)
        with conn.transaction():
            for security in securities:
                conn.execute(
                    _UPSERT,
                    (
                        security.ticker,
                        security.company_name,
                        list(security.aliases),
                        security.board,
                        source,
                        stamp,
                    ),
                )
            conn.execute(_RETIRE, (stamp, tickers))
    return len(securities)


def read(dsn: str) -> list[Security]:
    """The live registry, falling back to the committed baseline when Postgres is unreachable."""
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            rows = conn.execute(_SELECT_CURRENT).fetchall()
    except psycopg.Error:
        return load_baseline()
    if not rows:
        return load_baseline()
    return [
        Security(ticker=r[0], company_name=r[1], board=r[2], aliases=tuple(r[3] or ()))
        for r in rows
    ]


def refreshed_at(dsn: str) -> datetime | None:
    """When the registry last changed, so re-tagging knows what is stale."""
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(_MAX_REFRESHED_AT).fetchone()
    except psycopg.Error:
        return None
    return row[0] if row else None
