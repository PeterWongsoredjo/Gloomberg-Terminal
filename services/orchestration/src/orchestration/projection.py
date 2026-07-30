"""Writes freshly polled news items into the intraday Postgres projection.

Insert-only for content: a re-seen item never has its scoring state touched.
The agentic side owns scoring updates and the intraday.sentiment table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from pipeline.reference.matcher import Registry

_DDL = """
create schema if not exists intraday;

create table if not exists intraday.news_item (
    item_id text primary key,
    trade_date date not null,
    source text not null,
    lang text,
    title text not null,
    summary text,
    url text not null,
    published_at timestamptz not null,
    tickers text[] not null default '{}',
    ingest_run_id text,
    first_seen_at timestamptz not null default now(),
    score_attempts int not null default 0,
    last_attempt_run_id text,
    scored_at timestamptz,
    scored_run_id text
);

create index if not exists news_item_feed_idx
    on intraday.news_item (published_at desc, item_id desc);

alter table intraday.news_item add column if not exists candidate_tickers text[] not null default '{}';
alter table intraday.news_item add column if not exists tagged_at timestamptz;
alter table intraday.news_item add column if not exists score_status text not null default 'PENDING';

update intraday.news_item set score_status = 'SCORED'
    where scored_at is not null and score_status = 'PENDING';
update intraday.news_item set score_status = 'UNSCOREABLE'
    where scored_at is null and score_attempts >= 3 and score_status = 'PENDING';

drop index if exists intraday.news_item_unscored_idx;
create index if not exists news_item_pending_idx
    on intraday.news_item (trade_date, published_at, item_id) where score_status = 'PENDING';
"""

_INSERT = """
insert into intraday.news_item
    (item_id, trade_date, source, lang, title, summary, url, published_at,
     tickers, candidate_tickers, tagged_at, ingest_run_id)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (item_id) do nothing
returning item_id, tickers
"""

_STALE_FOR_RETAG = """
select item_id, title, summary from intraday.news_item
where tagged_at is null or tagged_at < %s
"""

_RETAG = """
update intraday.news_item
set tickers = %s, candidate_tickers = %s, tagged_at = %s
where item_id = %s
"""

_PENDING_COUNT = """
select count(*) from intraday.news_item
where trade_date = %s and score_status = 'PENDING'
"""

_PRUNE_ITEMS = "delete from intraday.news_item where trade_date < %s"
_PRUNE_SENTIMENT = "delete from intraday.sentiment where trade_date < %s"
_PRUNE_INSIGHT = "delete from intraday.insight where trade_date < %s"


@dataclass(frozen=True)
class InsertOutcome:
    new_item_ids: list[str]
    new_tickers: list[str]


def _published_at(raw: Any) -> datetime:
    parsed = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def upsert_items(
    dsn: str, items: list[dict[str, Any]], trade_date: date, ingest_run_id: str | None
) -> InsertOutcome:
    """Inserts the poll's items, returning only the ones never seen before."""
    new_ids: list[str] = []
    tickers: set[str] = set()
    tagged_at = datetime.now(timezone.utc)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(_DDL)
        with conn.transaction():
            for item in items:
                row = conn.execute(
                    _INSERT,
                    (
                        str(item["item_id"]),
                        trade_date,
                        str(item["source"]),
                        item.get("lang"),
                        str(item["title"]),
                        item.get("summary"),
                        str(item["url"]),
                        _published_at(item["published_at"]),
                        list(item.get("tickers") or []),
                        list(item.get("candidate_tickers") or []),
                        tagged_at,
                        ingest_run_id,
                    ),
                ).fetchone()
                if row is not None:
                    new_ids.append(str(row[0]))
                    tickers.update(str(t) for t in (row[1] or []))
    return InsertOutcome(new_item_ids=new_ids, new_tickers=sorted(tickers))


def retag_items(dsn: str, registry: Registry, registry_refreshed_at: datetime) -> int:
    """Re-resolves every item tagged before the registry last changed."""
    retagged = 0
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(_DDL)
        rows = conn.execute(_STALE_FOR_RETAG, (registry_refreshed_at,)).fetchall()
        with conn.transaction():
            for item_id, title, summary in rows:
                match = registry.match(str(title), str(summary or ""))
                conn.execute(
                    _RETAG, (match.tickers, match.candidates, registry_refreshed_at, item_id)
                )
                retagged += 1
    return retagged


def pending_count(dsn: str, trade_date: date) -> int:
    """How many of the day's articles still await a score."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute(_DDL)
        row = conn.execute(_PENDING_COUNT, (trade_date,)).fetchone()
    return int(row[0]) if row else 0


def prune(dsn: str, today: date, keep_days: int = 14) -> None:
    """Drops projection rows older than the retention window."""
    floor = date.fromordinal(today.toordinal() - keep_days)
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        conn.execute(_PRUNE_ITEMS, (floor,))
        for sql in (_PRUNE_SENTIMENT, _PRUNE_INSIGHT):
            try:
                conn.execute(sql, (floor,))
            except psycopg.errors.UndefinedTable:
                pass  # scored tables appear once the backend booted
