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
INTRADAY_OBJECTIVES = frozenset({"article_sentiment", "intraday_insight"})

# the objectives whose insight is projected for serving
INSIGHT_PROJECTED = frozenset({"intraday_insight", "insight_synthesis"})

# the objectives that read news from the poll projection, not Gold
POLLED_NEWS = frozenset({"article_sentiment", "intraday_insight", "insight_synthesis"})

# the objectives that read filed documents and no news at all
NO_NEWS = frozenset({"dividend_extraction"})

_DDL = """
create schema if not exists intraday;

do $$
begin
    if to_regclass('intraday.news_item') is not null then
        alter table intraday.news_item
            add column if not exists candidate_tickers text[] not null default '{}';
        alter table intraday.news_item add column if not exists tagged_at timestamptz;
        alter table intraday.news_item
            add column if not exists score_status text not null default 'PENDING';
        update intraday.news_item set score_status = 'SCORED'
            where scored_at is not null and score_status = 'PENDING';
    end if;
end $$;

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

alter table intraday.insight add column if not exists evidence_fingerprint text;
alter table intraday.insight
    add column if not exists watchpoints jsonb not null default '[]'::jsonb;

create table if not exists intraday.article_sentiment (
    item_id text primary key,
    trade_date date not null,
    artifact_id text not null,
    run_id text not null,
    trace_id text,
    batch_id text not null,
    sentiment_score double precision not null,
    sentiment_label text not null,
    drivers jsonb not null default '[]'::jsonb,
    dropped_tickers text[] not null default '{}',
    confidence double precision not null,
    quality_flags jsonb not null default '[]'::jsonb,
    provider text not null,
    model text not null,
    prompt_version text not null,
    published_at timestamptz not null,
    generated_at timestamptz not null
);

alter table intraday.article_sentiment add column if not exists rationale text;

create index if not exists article_sentiment_date_idx on intraday.article_sentiment (trade_date);

create table if not exists intraday.article_ticker_sentiment (
    item_id text not null references intraday.article_sentiment(item_id) on delete cascade,
    ticker text not null,
    trade_date date not null,
    sentiment_score double precision not null,
    sentiment_label text not null,
    relevance text not null,
    primary key (item_id, ticker)
);

create index if not exists ats_ticker_date_idx
    on intraday.article_ticker_sentiment (ticker, trade_date);

create table if not exists intraday.cash_dividend (
    filing_id text not null,
    event_seq int not null,
    trade_date date not null,
    ticker text not null,
    artifact_id text not null,
    run_id text not null,
    trace_id text,
    dividend_kind text not null,
    currency text not null,
    amount_text text not null,
    amount_per_share_sen bigint,
    ex_date date,
    recording_date date,
    payment_date date,
    source_span text not null,
    event_confidence double precision not null,
    confidence double precision not null,
    quality_flags jsonb not null default '[]'::jsonb,
    provider text not null,
    model text not null,
    prompt_version text not null,
    generated_at timestamptz not null,
    primary key (filing_id, event_seq)
);

create index if not exists cash_dividend_ticker_date_idx
    on intraday.cash_dividend (ticker, trade_date);
"""

_CLAIM_SQL = """
with claimed as (
    select item_id from intraday.news_item
    where trade_date = $1 and score_status = 'PENDING' and score_attempts < $2
    order by published_at, item_id
    limit $3
    for update skip locked
)
update intraday.news_item n
set score_attempts = n.score_attempts + 1,
    last_attempt_run_id = $4,
    score_status = case when n.score_attempts + 1 >= $2 then 'UNSCOREABLE' else 'PENDING' end
from claimed c
where n.item_id = c.item_id
returning n.item_id, n.trade_date, n.source, n.lang, n.title, n.summary, n.url,
          n.published_at, n.tickers, n.candidate_tickers
"""

_CLAIM_FILINGS_SQL = """
with claimed as (
    select filing_id from intraday.dividend_filing
    where trade_date = $1 and extract_status = 'PENDING' and extract_attempts < $2
    order by announced_at, filing_id
    limit $3
    for update skip locked
)
update intraday.dividend_filing f
set extract_attempts = f.extract_attempts + 1,
    last_attempt_run_id = $4,
    extract_status = case
        when f.extract_attempts + 1 >= $2 then 'UNEXTRACTABLE' else 'PENDING' end
from claimed c
where f.filing_id = c.filing_id
returning f.filing_id, f.ticker, f.title, f.filing_number, f.source_url,
          f.announced_at, f.body, f.char_count, f.extract_attempts
"""

_SETTLE_FILINGS_SQL = """
update intraday.dividend_filing
set extracted_at = now(), extracted_run_id = $2, extract_status = 'EXTRACTED'
where filing_id = any($1)
"""

_CASH_DIVIDEND_DELETE_SQL = "delete from intraday.cash_dividend where filing_id = $1"

_CASH_DIVIDEND_INSERT_SQL = """
insert into intraday.cash_dividend (
    filing_id, event_seq, trade_date, ticker, artifact_id, run_id, trace_id,
    dividend_kind, currency, amount_text, amount_per_share_sen, ex_date, recording_date,
    payment_date, source_span, event_confidence, confidence, quality_flags,
    provider, model, prompt_version, generated_at
)
values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
"""

_DAY_ITEMS_SQL = """
select item_id, trade_date, source, lang, title, summary, url, published_at, tickers
from intraday.news_item
where trade_date = $1
order by published_at, item_id
"""

_SETTLE_SQL = """
update intraday.news_item
set scored_at = now(), scored_run_id = $2, score_status = 'SCORED'
where item_id = any($1)
"""

_ARTICLE_UPSERT_SQL = """
insert into intraday.article_sentiment (
    item_id, trade_date, artifact_id, run_id, trace_id, batch_id, sentiment_score,
    sentiment_label, rationale, drivers, dropped_tickers, confidence, quality_flags,
    provider, model, prompt_version, published_at, generated_at
)
values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
on conflict (item_id) do update set
    artifact_id = excluded.artifact_id,
    run_id = excluded.run_id,
    trace_id = excluded.trace_id,
    batch_id = excluded.batch_id,
    sentiment_score = excluded.sentiment_score,
    sentiment_label = excluded.sentiment_label,
    rationale = excluded.rationale,
    drivers = excluded.drivers,
    dropped_tickers = excluded.dropped_tickers,
    confidence = excluded.confidence,
    quality_flags = excluded.quality_flags,
    provider = excluded.provider,
    model = excluded.model,
    prompt_version = excluded.prompt_version,
    generated_at = excluded.generated_at
where excluded.generated_at > intraday.article_sentiment.generated_at
"""

_TICKER_DELETE_SQL = "delete from intraday.article_ticker_sentiment where item_id = $1"

_TICKER_INSERT_SQL = """
insert into intraday.article_ticker_sentiment
    (item_id, ticker, trade_date, sentiment_score, sentiment_label, relevance)
values ($1,$2,$3,$4,$5,$6)
on conflict (item_id, ticker) do update set
    sentiment_score = excluded.sentiment_score,
    sentiment_label = excluded.sentiment_label,
    relevance = excluded.relevance
"""

_ROLLUP_SOURCE_SQL = """
select t.ticker, t.sentiment_score, t.relevance,
       a.item_id, a.confidence, a.published_at, a.artifact_id,
       a.provider, a.model, a.prompt_version
from intraday.article_ticker_sentiment t
join intraday.article_sentiment a on a.item_id = t.item_id
where t.trade_date = $1
"""

_INSIGHT_SUBJECTS_SQL = """
select t.ticker,
       count(*) as article_count,
       md5(string_agg(t.item_id, ',' order by t.item_id)) as evidence_fingerprint
from intraday.article_ticker_sentiment t
where t.trade_date = $1 and t.relevance <> 'INCIDENTAL'
group by t.ticker
order by article_count desc, t.ticker
"""

_INSIGHT_UPSERT_SQL = """
insert into intraday.insight (
    ticker, trade_date, artifact_id, run_id, trace_id, headline, narrative,
    contradictions, watchpoints, confidence, quality_flags, provider, model, prompt_version,
    generated_at, evidence_fingerprint
)
values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
    (select md5(string_agg(item_id, ',' order by item_id))
     from intraday.article_ticker_sentiment
     where ticker = $1 and trade_date = $2 and relevance <> 'INCIDENTAL'))
on conflict (ticker, trade_date) do update set
    evidence_fingerprint = excluded.evidence_fingerprint,
    artifact_id = excluded.artifact_id,
    run_id = excluded.run_id,
    trace_id = excluded.trace_id,
    headline = excluded.headline,
    narrative = excluded.narrative,
    contradictions = excluded.contradictions,
    watchpoints = excluded.watchpoints,
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


def _as_optional_date(value: Any) -> date | None:
    """A filing often states no date at all, and that stays a real null."""
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


async def claim_unscored_batch(
    pool: asyncpg.Pool, trade_date: str, max_attempts: int, cap: int, run_id: str
) -> list[dict[str, Any]]:
    """Takes the next slice of unscored articles for this run, spending their attempt now."""
    try:
        rows = await pool.fetch(_CLAIM_SQL, _as_date(trade_date), max_attempts, cap, run_id)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresConnectionError):
        return []
    return [dict(r) for r in rows]


async def claim_unextracted_filings(
    pool: asyncpg.Pool, trade_date: str, max_attempts: int, cap: int, run_id: str
) -> list[dict[str, Any]]:
    """Takes the next slice of unread filings for this run, spending their attempt now."""
    try:
        rows = await pool.fetch(
            _CLAIM_FILINGS_SQL, _as_date(trade_date), max_attempts, cap, run_id
        )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresConnectionError):
        return []
    return [dict(r) for r in rows]


def _cash_dividend_rows(
    artifact: dict[str, Any], state: AgentState
) -> list[tuple[Any, ...]]:
    """One database row per declared dividend line in one filing."""
    value = artifact["value"]
    provenance = artifact["provenance"]
    return [
        (
            str(value["filing_id"]),
            seq,
            _as_date(state["trade_date"]),
            str(event["ticker"]),
            str(artifact["artifact_id"]),
            state["run_id"],
            state.get("trace_id"),
            str(event["dividend_kind"]),
            str(event["currency"]),
            str(event["amount_text"]),
            event.get("amount_per_share_sen"),
            _as_optional_date(event.get("ex_date")),
            _as_optional_date(event.get("recording_date")),
            _as_optional_date(event.get("payment_date")),
            str(event["source_span"]),
            float(event["confidence"]),
            float(artifact["confidence"]),
            json.dumps(list(artifact.get("quality_flags") or [])),
            str(provenance["provider"]),
            str(provenance["model"]),
            str(provenance["prompt_version"]),
            _as_datetime(provenance["generated_at"]),
        )
        for seq, event in enumerate(value.get("events") or [])
    ]


async def project_cash_dividend(
    pool: asyncpg.Pool, state: AgentState, artifacts: list[dict[str, Any]]
) -> None:
    """Lands each filing's declared dividends and settles the filings, in one transaction."""
    extracted = [a for a in artifacts if a.get("artifact_type") == "CASH_DIVIDEND"]
    if not extracted:
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            settled = []
            for artifact in extracted:
                filing_id = str(artifact["value"]["filing_id"])
                # a re-read replaces the filing's lines rather than doubling them
                await conn.execute(_CASH_DIVIDEND_DELETE_SQL, filing_id)
                for row in _cash_dividend_rows(artifact, state):
                    await conn.execute(_CASH_DIVIDEND_INSERT_SQL, *row)
                settled.append(filing_id)
            await conn.execute(_SETTLE_FILINGS_SQL, settled, state["run_id"])


async def insight_subjects(pool: asyncpg.Pool, trade_date: str) -> list[dict[str, Any]]:
    """Tickers with real news today, and a fingerprint of the articles behind them."""
    try:
        rows = await pool.fetch(_INSIGHT_SUBJECTS_SQL, _as_date(trade_date))
    except (asyncpg.UndefinedTableError, asyncpg.PostgresConnectionError):
        return []
    return [dict(r) for r in rows]


async def rollup_source(pool: asyncpg.Pool, trade_date: str) -> list[dict[str, Any]]:
    """Every per-ticker article read for the day, the input to the deterministic rollup."""
    try:
        rows = await pool.fetch(_ROLLUP_SOURCE_SQL, _as_date(trade_date))
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


async def project(pool: asyncpg.Pool, state: AgentState, rows: list[tuple[Any, ...]]) -> None:
    """Lands rolled-up per-ticker sentiment rows, freshest wins."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                await conn.execute(_UPSERT_SQL, *row)


def _published_at_by_item(state: AgentState) -> dict[str, datetime]:
    return {
        str(n["item_id"]): _as_datetime(n["published_at"])
        for n in state["context"]["news_items"]
        if n.get("item_id") and n.get("published_at")
    }


def _article_row(
    artifact: dict[str, Any], state: AgentState, published_at: datetime, batch_id: str
) -> tuple[Any, ...]:
    """One upsert-ready row from an accepted article-sentiment artifact."""
    value = artifact["value"]
    provenance = artifact["provenance"]
    return (
        str(value["item_id"]),
        _as_date(state["trade_date"]),
        str(artifact["artifact_id"]),
        state["run_id"],
        state.get("trace_id"),
        batch_id,
        float(value["sentiment_score"]),
        str(value["sentiment_label"]),
        value.get("rationale"),
        json.dumps(value.get("drivers", [])),
        [str(t) for t in value.get("dropped_tickers", [])],
        float(artifact["confidence"]),
        json.dumps(artifact.get("quality_flags", [])),
        str(provenance["provider"]),
        str(provenance["model"]),
        str(provenance["prompt_version"]),
        published_at,
        _as_datetime(provenance["generated_at"]),
    )


async def project_article_sentiment(
    pool: asyncpg.Pool,
    state: AgentState,
    artifacts: list[dict[str, Any]],
    batch_ids: dict[str, str] | None = None,
) -> None:
    """Lands per-article verdicts and settles the items they scored, in one transaction."""
    scored = [a for a in artifacts if a.get("artifact_type") == "ARTICLE_SENTIMENT"]
    if not scored:
        return
    published = _published_at_by_item(state)
    fallback = datetime.now(timezone.utc)
    batches = batch_ids or {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            settled = []
            for artifact in scored:
                value = artifact["value"]
                item_id = str(value["item_id"])
                await conn.execute(
                    _ARTICLE_UPSERT_SQL,
                    *_article_row(
                        artifact,
                        state,
                        published.get(item_id, fallback),
                        batches.get(item_id, state["run_id"]),
                    ),
                )
                await conn.execute(_TICKER_DELETE_SQL, item_id)
                for entry in value.get("ticker_sentiments", []):
                    await conn.execute(
                        _TICKER_INSERT_SQL,
                        item_id,
                        str(entry["ticker"]),
                        _as_date(state["trade_date"]),
                        float(entry["sentiment_score"]),
                        str(entry["sentiment_label"]),
                        str(entry["relevance"]),
                    )
                settled.append(item_id)
            await conn.execute(_SETTLE_SQL, settled, state["run_id"])


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
        json.dumps(value.get("watchpoints", [])),
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
