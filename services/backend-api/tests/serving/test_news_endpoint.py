"""GET /news over a hermetic Gold snapshot, with no Postgres behind it.

Proves a corporate action reaches the feed as its own item type, interleaved with
articles by published_at, and that a not-yet-built corporate-action table costs only
those rows rather than blanking the whole feed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import duckdb
import httpx
import pytest

from app.api.v1.deps import get_app_state
from app.lifespan import AppState
from app.main import app

pytestmark = pytest.mark.anyio

_NEWS_DDL = """
create table fct_news_item(
    item_id varchar, trade_date date, source varchar, lang varchar, title varchar,
    summary varchar, url varchar, published_at timestamp, tickers varchar[]
)
"""

_CORP_DDL = """
create table fct_corporate_action_news(
    item_id varchar, trade_date date, source varchar, lang varchar, title varchar,
    summary varchar, url varchar, published_at timestamp, tickers varchar[]
)
"""

_SENTIMENT_DDL = """
create table fct_article_sentiment(
    item_id varchar, sentiment_score double, sentiment_label varchar, confidence double,
    artifact_id varchar, provider varchar, model varchar, prompt_version varchar,
    generated_at timestamp, rationale varchar, drivers varchar[]
)
"""

_TICKER_DDL = """
create table fct_article_ticker_sentiment(
    item_id varchar, ticker varchar, sentiment_score double, sentiment_label varchar,
    relevance varchar
)
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _snapshot(with_corporate_actions: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(_NEWS_DDL)
    con.execute(_SENTIMENT_DDL)
    con.execute(_TICKER_DDL)
    con.execute(
        "insert into fct_news_item values "
        "('cnbc:a1','2026-07-10','cnbc_market','id','BBCA laba naik','ringkasan',"
        "'https://example.test/a','2026-07-10 03:00:00',['BBCA'])"
    )
    if with_corporate_actions:
        con.execute(_CORP_DDL)
        con.execute(
            "insert into fct_corporate_action_news values "
            "('corp_action:idx_ca:999001','2026-07-10','idx_corporate_action','en',"
            "'AADI stock split, effective 10 Jul 2026','IDX has recorded a stock split for AADI.',"
            "null,'2026-07-10 00:00:00',['AADI'])"
        )
        con.execute(
            "insert into fct_article_sentiment values "
            "('corp_action:idx_ca:999001',0.22,'BULLISH',0.66,'art1','gemini','g','art-sent-v3',"
            "'2026-07-10 09:00:00','A five-for-one split widens retail access.',['stock split'])"
        )
        con.execute(
            "insert into fct_article_ticker_sentiment values "
            "('corp_action:idx_ca:999001','AADI',0.22,'BULLISH','PRIMARY')"
        )
    return con


async def _get_news(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    state = AppState(duckdb_ro=con, pg_pool=None, slo_engine=None)
    app.dependency_overrides[get_app_state] = lambda: state
    try:
        async for client in _client():
            response = await client.get("/api/v1/news?limit=20")
            assert response.status_code == 200
            body: dict[str, Any] = response.json()
            return body
    finally:
        app.dependency_overrides.pop(get_app_state, None)
    raise AssertionError("no response")


async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_a_corporate_action_reaches_the_news_feed() -> None:
    """The DoD case: a corporate action is served, typed, and carries its sentiment."""
    body = await _get_news(_snapshot())
    rows = body["data"]["rows"]
    corp = [r for r in rows if r["item_type"] == "CORPORATE_ACTION"]

    assert len(corp) == 1
    assert corp[0]["item_id"] == "corp_action:idx_ca:999001"
    assert corp[0]["title"] == "AADI stock split, effective 10 Jul 2026"
    assert corp[0]["url"] is None
    assert corp[0]["tickers"] == ["AADI"]
    assert corp[0]["sentiment_label"] == "BULLISH"
    assert corp[0]["sentiment_score"] == pytest.approx(0.22)
    assert corp[0]["ticker_sentiments"][0]["relevance"] == "PRIMARY"


async def test_an_article_stays_typed_as_an_article() -> None:
    """The discriminator has to distinguish, not just exist."""
    rows = (await _get_news(_snapshot()))["data"]["rows"]
    article = [r for r in rows if r["item_id"] == "cnbc:a1"]

    assert len(article) == 1
    assert article[0]["item_type"] == "ARTICLE"
    assert article[0]["url"] == "https://example.test/a"


async def test_both_kinds_interleave_newest_first() -> None:
    """Corporate actions merge into the one ordering, they are not a separate list."""
    rows = (await _get_news(_snapshot()))["data"]["rows"]

    assert [r["item_id"] for r in rows] == ["cnbc:a1", "corp_action:idx_ca:999001"]


async def test_an_unbuilt_corporate_action_table_leaves_the_articles_alone() -> None:
    """The reason these are two reads and not one union: articles must survive."""
    rows = (await _get_news(_snapshot(with_corporate_actions=False)))["data"]["rows"]

    assert [r["item_id"] for r in rows] == ["cnbc:a1"]
    assert rows[0]["item_type"] == "ARTICLE"
