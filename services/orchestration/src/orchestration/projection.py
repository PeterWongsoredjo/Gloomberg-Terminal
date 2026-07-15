"""Writes freshly polled news items into the intraday Postgres projection.

Insert-only for content: a re-seen item never has its scoring state touched.
The agentic side owns scoring updates and the intraday.sentiment table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import psycopg

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

create index if not exists news_item_unscored_idx
    on intraday.news_item (trade_date) where scored_at is null;
"""

_INSERT = """
insert into intraday.news_item
    (item_id, trade_date, source, lang, title, summary, url, published_at, tickers, ingest_run_id)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (item_id) do nothing
returning item_id, tickers
"""

_PRUNE_ITEMS = "delete from intraday.news_item where trade_date < %s"
_PRUNE_SENTIMENT = "delete from intraday.sentiment where trade_date < %s"


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
                        ingest_run_id,
                    ),
                ).fetchone()
                if row is not None:
                    new_ids.append(str(row[0]))
                    tickers.update(str(t) for t in (row[1] or []))
    return InsertOutcome(new_item_ids=new_ids, new_tickers=sorted(tickers))


def prune(dsn: str, today: date, keep_days: int = 14) -> None:
    """Drops projection rows older than the retention window."""
    floor = date.fromordinal(today.toordinal() - keep_days)
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        conn.execute(_PRUNE_ITEMS, (floor,))
        try:
            conn.execute(_PRUNE_SENTIMENT, (floor,))
        except psycopg.errors.UndefinedTable:
            pass  # sentiment table appears once the backend booted
