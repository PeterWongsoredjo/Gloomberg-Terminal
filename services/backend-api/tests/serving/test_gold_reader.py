"""The Gold reader's tolerance for a snapshot the build has not caught up to yet."""

from __future__ import annotations

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
