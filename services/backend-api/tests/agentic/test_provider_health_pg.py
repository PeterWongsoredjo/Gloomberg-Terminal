"""The provider_health day migration, against a live Postgres (skipped when the container is down).

A quota counter belongs to one day. Before this, the table was keyed on provider alone and the
boot-time reseed read it with no date filter, so a restart could inherit a spent day forever.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.agentic import ledger
from app.agentic.config import get_agentic_settings


async def _pool_or_skip() -> asyncpg.Pool:
    """Opens the container Postgres pool, or skips when it is down."""
    settings = get_agentic_settings()
    try:
        return await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


async def _primary_key_columns(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(
        """
        select a.attname from pg_constraint c
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
        where c.conrelid = 'agentic.provider_health'::regclass and c.contype = 'p'
        order by a.attname
        """
    )
    return [str(r["attname"]) for r in rows]


async def test_the_old_single_column_key_migrates_and_keeps_its_real_day() -> None:
    """A pre-migration row must land on the day it was written, not on today."""
    pool = await _pool_or_skip()
    try:
        await pool.execute("drop table if exists agentic.provider_health cascade")
        await pool.execute("create schema if not exists agentic")
        # the shape this table had before the fix
        await pool.execute(
            """
            create table agentic.provider_health (
                provider text primary key,
                breaker_state text not null,
                consecutive_failures int not null default 0,
                rpd_consumed int not null default 0,
                updated_at timestamptz not null default now()
            )
            """
        )
        await pool.execute(
            """
            insert into agentic.provider_health (provider, breaker_state, rpd_consumed, updated_at)
            values ('groq', 'CLOSED', 990, now() - interval '9 days')
            """
        )

        await ledger.setup(pool)

        assert await _primary_key_columns(pool) == ["day", "provider"]
        row = await pool.fetchrow("select day, rpd_consumed from agentic.provider_health")
        assert row is not None and int(row["rpd_consumed"]) == 990
        today = await pool.fetchval("select (now() at time zone 'Asia/Jakarta')::date")
        assert row["day"] != today  # the spent day stayed in the past

        # so a boot today never inherits it
        assert await ledger.read_provider_consumption(pool) == {}
    finally:
        await pool.execute("drop table if exists agentic.provider_health cascade")
        await ledger.setup(pool)
        await pool.close()


async def test_a_days_counters_are_written_and_read_back_for_today() -> None:
    """Today's row is the one the reseed picks up, and re-recording updates it in place."""
    pool = await _pool_or_skip()
    try:
        await ledger.setup(pool)
        await pool.execute("delete from agentic.provider_health where provider = 'test_provider'")
        for spent in (10, 40):
            await ledger.record_provider_health(
                pool,
                provider="test_provider",
                breaker_state="CLOSED",
                consecutive_failures=0,
                rpd_consumed=spent,
                tpd_consumed=spent * 100,
            )
        assert (await ledger.read_provider_consumption(pool))["test_provider"] == (40, 4000)
        assert await pool.fetchval(
            "select count(*) from agentic.provider_health where provider = 'test_provider'"
        ) == 1
    finally:
        await pool.execute("delete from agentic.provider_health where provider = 'test_provider'")
        await pool.close()
