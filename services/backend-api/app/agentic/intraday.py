"""
In-session projections: polled news in, scored sentiment and insight rows out.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import asyncpg

from app.agentic.state import AgentState

# the objectives that run in session and land in the intraday schema
INTRADAY_OBJECTIVES = frozenset({"intraday_sentiment", "intraday_insight"})

_DDL = """
create schema if not exists intraday;

create table if not exists intraday.sentiment (
    ticker text not null,
    trade_date date not null,
    artifact_id text not null,
    run_id text not null,
    trace_id text,
    sentiment_score double precision not null,
    sentiment_label text not null,
    confidence double precision not null,
    quality_flags jsonb not null default '[]'::jsonb,
    provider text not null,
    model text not null,
    prompt_version text not null,
    evidence_item_ids text[] not null default '{}',
    generated_at timestamptz not null,
    primary key (ticker, trade_date)
);

create table if not exists intraday.insight (
    ticker text not null,
    trade_date date not null,
    artifact_id text not null,
    run_id text not null,
    trace_id text,
    headline text not null,
    narrative text not null,
    contradictions jsonb not null default '[]'::jsonb,
    confidence double precision not null,
    quality_flags jsonb not null default '[]'::jsonb,
    provider text not null,
    model text not null,
    prompt_version text not null,
    generated_at timestamptz not null,
    primary key (ticker, trade_date)
);
"""

_UNSCORED_SQL = """
select item_id, trade_date, source, lang, title, summary, url, published_at, tickers
from intraday.news_item
where trade_date = $1 and scored_at is null and score_attempts < $2
order by published_at, item_id
"""

_DAY_ITEMS_SQL = """
select item_id, trade_date, source, lang, title, summary, url, published_at, tickers
from intraday.news_item
where trade_date = $1
order by published_at, item_id
"""

_ATTEMPT_SQL = """
update intraday.news_item
set score_attempts = score_attempts + 1, last_attempt_run_id = $2
where item_id = any($1)
"""

_SCORED_SQL = """
update intraday.news_item
set scored_at = now(), scored_run_id = $2
where item_id = any($1) and scored_at is null
"""

_INSIGHT_UPSERT_SQL = """
insert into intraday.insight (
    ticker, trade_date, artifact_id, run_id, trace_id, headline, narrative,
    contradictions, confidence, quality_flags, provider, model, prompt_version, generated_at
)
values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
on conflict (ticker, trade_date) do update set
    artifact_id = excluded.artifact_id,
    run_id = excluded.run_id,
    trace_id = excluded.trace_id,
    headline = excluded.headline,
    narrative = excluded.narrative,
    contradictions = excluded.contradictions,
    confidence = excluded.confidence,
    quality_flags = excluded.quality_flags,
    provider = excluded.provider,
    model = excluded.model,
    prompt_version = excluded.prompt_version,
    generated_at = excluded.generated_at
where excluded.generated_at > intraday.insight.generated_at
"""

_UPSERT_SQL = """
insert into intraday.sentiment (
    ticker, trade_date, artifact_id, run_id, trace_id, sentiment_score, sentiment_label,
    confidence, quality_flags, provider, model, prompt_version, evidence_item_ids, generated_at
)
values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
on conflict (ticker, trade_date) do update set
    artifact_id = excluded.artifact_id,
    run_id = excluded.run_id,
    trace_id = excluded.trace_id,
    sentiment_score = excluded.sentiment_score,
    sentiment_label = excluded.sentiment_label,
    confidence = excluded.confidence,
    quality_flags = excluded.quality_flags,
    provider = excluded.provider,
    model = excluded.model,
    prompt_version = excluded.prompt_version,
    evidence_item_ids = excluded.evidence_item_ids,
    generated_at = excluded.generated_at
where excluded.generated_at > intraday.sentiment.generated_at
"""


async def setup(pool: asyncpg.Pool) -> None:
    await pool.execute(_DDL)


def _as_date(value: str) -> date:
    return date.fromisoformat(value)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def unscored_items(pool: asyncpg.Pool, trade_date: str, max_attempts: int) -> list[dict[str, Any]]:
    """The day's polled articles no run has scored yet."""
    try:
        rows = await pool.fetch(_UNSCORED_SQL, _as_date(trade_date), max_attempts)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresConnectionError):
        return []
    return [dict(r) for r in rows]


async def day_items(pool: asyncpg.Pool, trade_date: str) -> list[dict[str, Any]]:
    """Every article polled today, for the periodic insight refresh."""
    try:
        rows = await pool.fetch(_DAY_ITEMS_SQL, _as_date(trade_date))
    except (asyncpg.UndefinedTableError, asyncpg.PostgresConnectionError):
        return []
    return [dict(r) for r in rows]


def _sentiment_row(artifact: dict[str, Any], state: AgentState) -> tuple[Any, ...] | None:
    """One upsert-ready row from an accepted sentiment artifact dict."""
    ticker = (artifact.get("subject") or {}).get("ticker")
    value = artifact.get("value") or {}
    if artifact.get("artifact_type") != "SENTIMENT" or not ticker:
        return None
    provenance = artifact["provenance"]
    return (
        str(ticker),
        _as_date(state["trade_date"]),
        str(artifact["artifact_id"]),
        state["run_id"],
        state.get("trace_id"),
        float(value["sentiment_score"]),
        str(value["sentiment_label"]),
        float(artifact["confidence"]),
        json.dumps(artifact.get("quality_flags", [])),
        str(provenance["provider"]),
        str(provenance["model"]),
        str(provenance["prompt_version"]),
        [str(i) for i in value.get("evidence_item_ids", [])],
        _as_datetime(provenance["generated_at"]),
    )


async def project(pool: asyncpg.Pool, state: AgentState, artifacts: list[dict[str, Any]]) -> None:
    """Lands scored rows and updates the items' scoring lifecycle atomically."""
    consumed = sorted({str(n["item_id"]) for n in state["context"]["news_items"] if n.get("item_id")})
    scored = sorted({ref for a in artifacts for ref in a["provenance"].get("input_source_refs", [])})
    rows = [r for r in (_sentiment_row(a, state) for a in artifacts) if r is not None]

    async with pool.acquire() as conn:
        async with conn.transaction():
            if consumed:
                await conn.execute(_ATTEMPT_SQL, consumed, state["run_id"])
            if scored:
                await conn.execute(_SCORED_SQL, scored, state["run_id"])
            for row in rows:
                await conn.execute(_UPSERT_SQL, *row)


def _insight_row(artifact: dict[str, Any], state: AgentState) -> tuple[Any, ...] | None:
    """One upsert-ready row from an accepted insight artifact dict."""
    ticker = (artifact.get("subject") or {}).get("ticker")
    value = artifact.get("value") or {}
    if artifact.get("artifact_type") != "INSIGHT" or not ticker:
        return None
    provenance = artifact["provenance"]
    return (
        str(ticker),
        _as_date(state["trade_date"]),
        str(artifact["artifact_id"]),
        state["run_id"],
        state.get("trace_id"),
        str(value["headline"]),
        str(value["narrative"]),
        json.dumps(value.get("contradictions", [])),
        float(artifact["confidence"]),
        json.dumps(artifact.get("quality_flags", [])),
        str(provenance["provider"]),
        str(provenance["model"]),
        str(provenance["prompt_version"]),
        _as_datetime(provenance["generated_at"]),
    )


async def project_insight(pool: asyncpg.Pool, state: AgentState, artifacts: list[dict[str, Any]]) -> None:
    """Lands insight rows freshest-wins, never touching the news scoring lifecycle."""
    rows = [r for r in (_insight_row(a, state) for a in artifacts) if r is not None]
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                await conn.execute(_INSIGHT_UPSERT_SQL, *row)
