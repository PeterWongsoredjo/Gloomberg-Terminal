"""ledger just logs stuff the LLM agent does and saves to a DB:

four main tables are:
1. agent_run: keeps track of the agent runs (status, total tokens, etc.)
2. agent_artifact: stores actual outputs generated (sentiment score, etc.)
3. provider_health: keeps track of the provider health (breaker state, rate limits, etc.)
4. artifact_cache: caches outputs
"""

from __future__ import annotations

import json
from datetime import date

import asyncpg

from app.agentic.schemas import Ct009Artifact

_DDL = """
create schema if not exists agentic;

create table if not exists agentic.agent_run (
    run_id text primary key,
    objective text not null,
    trade_date date not null,
    status text not null,
    abort_reason text,
    consumed_tokens bigint not null default 0,
    consumed_iterations int not null default 0,
    started_at timestamptz not null,
    ended_at timestamptz,
    trace_id text
);

create table if not exists agentic.agent_artifact (
    artifact_id text primary key,
    run_id text not null references agentic.agent_run(run_id),
    artifact_type text not null,
    security_id bigint,
    ticker text,
    window_from date not null,
    window_to date not null,
    value jsonb not null,
    confidence double precision not null,
    quality_flags jsonb not null default '[]'::jsonb,
    provider text not null,
    model text not null,
    prompt_version text not null,
    token_usage jsonb not null default '{}'::jsonb,
    generated_at timestamptz not null
);

create table if not exists agentic.provider_health (
    provider text primary key,
    breaker_state text not null,
    consecutive_failures int not null default 0,
    rpd_consumed int not null default 0,
    tpd_consumed bigint not null default 0,
    updated_at timestamptz not null default now()
);

alter table agentic.provider_health add column if not exists tpd_consumed bigint not null default 0;

create table if not exists agentic.artifact_cache (
    cache_key text primary key,
    artifacts jsonb not null,
    stored_at timestamptz not null default now()
);
"""


async def setup(pool: asyncpg.Pool) -> None:
    await pool.execute(_DDL)


async def start_run(
    pool: asyncpg.Pool, *, run_id: str, objective: str, trade_date: date, trace_id: str | None
) -> None:
    await pool.execute(
        """
        insert into agentic.agent_run (run_id, objective, trade_date, status, started_at, trace_id)
        values ($1, $2, $3, 'RUNNING', now(), $4)
        on conflict (run_id) do update
            set trace_id = coalesce(excluded.trace_id, agentic.agent_run.trace_id)
        """,
        run_id,
        objective,
        trade_date,
        trace_id,
    )


async def finish_run(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    status: str,
    abort_reason: str | None,
    consumed_tokens: int,
    consumed_iterations: int,
) -> None:
    await pool.execute(
        """
        update agentic.agent_run
        set status = $2, abort_reason = $3, consumed_tokens = $4,
            consumed_iterations = $5, ended_at = now()
        where run_id = $1
        """,
        run_id,
        status,
        abort_reason,
        consumed_tokens,
        consumed_iterations,
    )


async def read_run(pool: asyncpg.Pool, run_id: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        select run_id, objective, trade_date, status, abort_reason,
               consumed_tokens, consumed_iterations, started_at, ended_at
        from agentic.agent_run
        where run_id = $1
        """,
        run_id,
    )


async def write_artifact(pool: asyncpg.Pool, run_id: str, artifact: Ct009Artifact) -> None:
    await pool.execute(
        """
        insert into agentic.agent_artifact (
            artifact_id, run_id, artifact_type, security_id, ticker, window_from, window_to,
            value, confidence, quality_flags, provider, model, prompt_version, token_usage,
            generated_at
        )
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        on conflict (artifact_id) do nothing
        """,
        artifact.artifact_id,
        run_id,
        artifact.artifact_type,
        artifact.subject.security_id,
        artifact.subject.ticker,
        artifact.window.from_,
        artifact.window.to,
        artifact.value.model_dump_json(),
        artifact.confidence,
        json.dumps([f.value for f in artifact.quality_flags]),
        artifact.provenance.provider,
        artifact.provenance.model,
        artifact.provenance.prompt_version,
        artifact.provenance.token_usage.model_dump_json(),
        artifact.provenance.generated_at,
    )


async def record_provider_health(
    pool: asyncpg.Pool,
    *,
    provider: str,
    breaker_state: str,
    consecutive_failures: int,
    rpd_consumed: int,
    tpd_consumed: int,
) -> None:
    await pool.execute(
        """
        insert into agentic.provider_health
            (provider, breaker_state, consecutive_failures, rpd_consumed, tpd_consumed, updated_at)
        values ($1, $2, $3, $4, $5, now())
        on conflict (provider) do update set
            breaker_state = excluded.breaker_state,
            consecutive_failures = excluded.consecutive_failures,
            rpd_consumed = excluded.rpd_consumed,
            tpd_consumed = excluded.tpd_consumed,
            updated_at = now()
        """,
        provider,
        breaker_state,
        consecutive_failures,
        rpd_consumed,
        tpd_consumed,
    )


async def read_provider_consumption(pool: asyncpg.Pool) -> dict[str, tuple[int, int]]:
    """Today's stored (requests, tokens) per provider, to reseed the quota tracker on boot."""
    rows = await pool.fetch("select provider, rpd_consumed, tpd_consumed from agentic.provider_health")
    return {str(r["provider"]): (int(r["rpd_consumed"]), int(r["tpd_consumed"])) for r in rows}
