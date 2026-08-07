"""The Gold reader's tolerance for a snapshot the build has not caught up to yet."""

from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from app.serving.readers.gold import ServingGoldReader

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stale_snapshot() -> duckdb.DuckDBPyConnection:
    """A published snapshot built before the newest columns existed."""
    con = duckdb.connect()
    con.execute(
        "create table fct_article_sentiment(item_id varchar, sentiment_score double, "
        "sentiment_label varchar, confidence double, artifact_id varchar, provider varchar, "
        "model varchar, prompt_version varchar, generated_at timestamp, drivers varchar[])"
    )
    con.execute(
        "create table fct_insight(artifact_id varchar, ticker varchar, trade_date date, "
        "prompt_version varchar, headline varchar, narrative varchar, contradictions varchar[], "
        "confidence double, provider varchar, model varchar, generated_at timestamp, dq_flags varchar[])"
    )
    con.execute("insert into fct_insight values ('a1','BBCA','2026-07-30','ins-v1','h','n',[],0.7,'groq','m','2026-07-30 10:00:00',[])")
    return con


async def test_a_missing_column_degrades_instead_of_killing_the_news_feed(
    stale_snapshot: duckdb.DuckDBPyConnection,
) -> None:
    """A column the build has not added yet raises BinderException, not CatalogException."""
    reader = ServingGoldReader(stale_snapshot)
    assert await reader.article_sentiment(["n1"]) == {}


async def test_a_missing_column_degrades_the_insight_read(
    stale_snapshot: duckdb.DuckDBPyConnection,
) -> None:
    """Serving falls back to the intraday projection rather than 500ing."""
    reader = ServingGoldReader(stale_snapshot)
    assert await reader.insight("BBCA") is None


async def test_a_missing_table_still_degrades(
    stale_snapshot: duckdb.DuckDBPyConnection,
) -> None:
    """The original not-yet-published-table tolerance must survive the widening."""
    reader = ServingGoldReader(duckdb.connect())
    assert await reader.article_sentiment(["n1"]) == {}
    assert await reader.insight("BBCA") is None
    assert await reader.news(10, None) == []
    assert await reader.corporate_action_news(10, None) == []


@pytest.fixture
def feed_snapshot() -> duckdb.DuckDBPyConnection:
    """A snapshot carrying both feed tables, three items apiece to page through."""
    con = duckdb.connect()
    for table in ("fct_news_item", "fct_corporate_action_news"):
        con.execute(
            f"create table {table}(item_id varchar, trade_date date, source varchar, "
            "lang varchar, title varchar, summary varchar, url varchar, "
            "published_at timestamp, tickers varchar[])"
        )
    con.execute(
        "insert into fct_news_item values "
        "('a1','2026-07-10','cnbc','id','t1',null,'u','2026-07-10 03:00:00',[]),"
        "('a2','2026-07-09','cnbc','id','t2',null,'u','2026-07-09 03:00:00',[])"
    )
    con.execute(
        "insert into fct_corporate_action_news values "
        "('corp_action:c1','2026-07-10','idx_corporate_action','en','AADI stock split',"
        "'s',null,'2026-07-10 00:00:00',['AADI'])"
    )
    return con


async def test_each_feed_read_stamps_its_own_item_type(
    feed_snapshot: duckdb.DuckDBPyConnection,
) -> None:
    """The discriminator is stamped by the reader, so fct_news_item needs no new column."""
    reader = ServingGoldReader(feed_snapshot)

    assert {r["item_type"] for r in await reader.news(10, None)} == {"ARTICLE"}
    corp = await reader.corporate_action_news(10, None)
    assert {r["item_type"] for r in corp} == {"CORPORATE_ACTION"}
    assert corp[0]["url"] is None


async def test_the_cursor_clause_pages_both_feeds_identically(
    feed_snapshot: duckdb.DuckDBPyConnection,
) -> None:
    """Both reads must honour the same cursor or the merged page loses rows."""
    reader = ServingGoldReader(feed_snapshot)
    before = (datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc), "a1")

    assert [r["item_id"] for r in await reader.news(10, before)] == ["a2"]
    assert [r["item_id"] for r in await reader.corporate_action_news(10, before)] == [
        "corp_action:c1"
    ]
