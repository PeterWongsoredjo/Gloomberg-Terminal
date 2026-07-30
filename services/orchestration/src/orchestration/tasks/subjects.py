"""
Picks which tickers are worth an insight run, always inside the curated universe.
"""

from __future__ import annotations

from datetime import date

import psycopg
from prefect import task

from pipeline.config import get_settings

from orchestration.results import PhaseResult

_STALE_SUBJECTS_SQL = """
with scored as (
    select ticker,
           count(*) as article_count,
           md5(string_agg(item_id, ',' order by item_id)) as fingerprint
    from intraday.article_ticker_sentiment
    where trade_date = %s and relevance <> 'INCIDENTAL' and ticker = any(%s)
    group by ticker
)
select s.ticker
from scored s
left join intraday.insight i on i.ticker = s.ticker and i.trade_date = %s
where i.evidence_fingerprint is distinct from s.fingerprint
order by s.article_count desc, s.ticker
limit %s
"""

_DAY_SUBJECTS_SQL = """
select ticker, count(*) as article_count
from intraday.article_ticker_sentiment
where trade_date = %s and relevance <> 'INCIDENTAL' and ticker = any(%s)
group by ticker
order by article_count desc, ticker
limit %s
"""

_EMPTY_UNIVERSE_NOTE = "universe empty, insight skipped"


def _query(dsn: str, sql: str, params: tuple[object, ...]) -> list[str]:
    """Runs one subject query, degrading to no subjects when Postgres is unreachable."""
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            rows = conn.execute(sql, params).fetchall()
    except psycopg.Error:
        return []
    return [str(r[0]) for r in rows]


def stale_insight_subjects(dsn: str, trade_date: date, cap: int, universe: list[str]) -> list[str]:
    """Universe tickers with news the current insight has not seen yet, busiest first."""
    if not universe:
        return []
    return _query(dsn, _STALE_SUBJECTS_SQL, (trade_date, universe, trade_date, cap))


def day_insight_subjects(dsn: str, trade_date: date, cap: int, universe: list[str]) -> list[str]:
    """Every universe ticker the day gave news to, busiest first."""
    if not universe:
        return []
    return _query(dsn, _DAY_SUBJECTS_SQL, (trade_date, universe, cap))


@task(name="insight_subjects")
def insight_subjects(trade_date: date, cap: int, universe: list[str]) -> PhaseResult:
    """Reports which subjects the hourly insight refresh should cover."""
    tickers = stale_insight_subjects(get_settings().postgres_dsn, trade_date, cap, universe)
    notes = _EMPTY_UNIVERSE_NOTE if not universe else f"{len(tickers)} subjects with unseen news"
    return PhaseResult(status="SUCCESS", payload=tickers, notes=notes)


@task(name="eod_insight_subjects")
def eod_insight_subjects(trade_date: date, cap: int, universe: list[str]) -> PhaseResult:
    """Reports which subjects the end-of-day conclusion should cover."""
    tickers = day_insight_subjects(get_settings().postgres_dsn, trade_date, cap, universe)
    notes = _EMPTY_UNIVERSE_NOTE if not universe else f"{len(tickers)} subjects with news today"
    return PhaseResult(status="SUCCESS", payload=tickers, notes=notes)
