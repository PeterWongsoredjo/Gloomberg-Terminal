"""
manages states of prompt templates from draft to evaluated, rejected, live, or deprecated
interacts with postgres to swap active prompts when promoted and queries live objectives
"""

from __future__ import annotations

import json

import asyncpg

STATES = ("DRAFT", "EVALUATED", "REGISTERED", "LIVE", "DEPRECATED", "REJECTED")

_DDL = """
create schema if not exists observability;

create table if not exists observability.prompt_version (
    objective text not null,
    version text not null,
    content_sha256 text not null,
    state text not null,
    mlflow_run_id text,
    reasons jsonb not null default '[]'::jsonb,
    evaluated_at timestamptz,
    live_at timestamptz,
    updated_at timestamptz not null default now(),
    primary key (objective, version)
);
"""


async def setup(pool: asyncpg.Pool) -> None:
    await pool.execute(_DDL)


async def register_draft(pool: asyncpg.Pool, *, objective: str, version: str, content_sha256: str) -> None:
    await pool.execute(
        """
        insert into observability.prompt_version (objective, version, content_sha256, state)
        values ($1, $2, $3, 'DRAFT')
        on conflict (objective, version) do nothing
        """,
        objective,
        version,
        content_sha256,
    )


async def mark_evaluated(pool: asyncpg.Pool, *, objective: str, version: str, mlflow_run_id: str) -> None:
    await pool.execute(
        """
        update observability.prompt_version
        set state = 'EVALUATED', mlflow_run_id = $3, evaluated_at = now(), updated_at = now()
        where objective = $1 and version = $2
        """,
        objective,
        version,
        mlflow_run_id,
    )


async def promote_to_live(pool: asyncpg.Pool, *, objective: str, version: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                update observability.prompt_version
                set state = 'DEPRECATED', updated_at = now()
                where objective = $1 and state = 'LIVE'
                """,
                objective,
            )
            await conn.execute(
                """
                update observability.prompt_version
                set state = 'LIVE', live_at = now(), updated_at = now()
                where objective = $1 and version = $2
                """,
                objective,
                version,
            )


async def reject(pool: asyncpg.Pool, *, objective: str, version: str, reasons: list[str]) -> None:
    await pool.execute(
        """
        update observability.prompt_version
        set state = 'REJECTED', reasons = $3::jsonb, updated_at = now()
        where objective = $1 and version = $2
        """,
        objective,
        version,
        json.dumps(reasons),
    )


async def state_of(pool: asyncpg.Pool, *, objective: str, version: str) -> str | None:
    row = await pool.fetchrow(
        "select state from observability.prompt_version where objective = $1 and version = $2",
        objective,
        version,
    )
    return None if row is None else str(row["state"])


async def live_versions(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch("select objective, version from observability.prompt_version where state = 'LIVE'")
    return {str(r["objective"]): str(r["version"]) for r in rows}
