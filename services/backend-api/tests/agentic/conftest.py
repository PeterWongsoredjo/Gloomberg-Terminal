"""Shared fixtures for the agentic suite: an in-memory Gold, scripted providers, and deps.

Everything here is deterministic and offline. The scripted provider stands in for a live LLM so
every fault path (rate limit, malformed output, outage) is reproducible without a network call.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import duckdb
import pytest

# the psycopg checkpointer needs a selector loop; Windows defaults to a proactor one
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.providers.base import ProviderRequest, ProviderResponse, ProviderSlot
from app.agentic.providers.breaker import CircuitBreaker
from app.agentic.providers.limits import BreakerConfig
from app.agentic.providers.pacer import RatePacer

Responder = Callable[[ProviderRequest], ProviderResponse]


class ScriptedProvider:
    """A provider whose every call is decided by an injected responder (may raise)."""

    def __init__(self, name: str, responder: Responder) -> None:
        self.name = name
        self._responder = responder

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return self._responder(request)


class RecordingTracer:
    """A tracer that records the name of every node span opened."""

    def __init__(self) -> None:
        self.spans: list[str] = []

    @asynccontextmanager
    async def span(self, name: str, run_id: str, inputs: dict[str, Any] | None = None) -> Any:
        self.spans.append(name)
        yield _RecordingSpan()


class _RecordingSpan:
    def set_output(self, output: Any) -> None: ...
    def set_usage(self, prompt_tokens: int, completion_tokens: int) -> None: ...
    def set_error(self, message: str) -> None: ...


def sentiment_response(
    *,
    provider: str = "groq",
    label: str = "BULLISH",
    score: float = 0.3,
    drivers: list[str] | None = None,
    evidence: list[str] | None = None,
    confidence: float = 0.8,
    tokens: int = 120,
    invalid: bool = False,
) -> ProviderResponse:
    """Builds a normalized sentiment response, or an unparseable one when invalid."""
    parsed: dict[str, Any] | None
    if invalid:
        parsed = {"totally": "wrong", "shape": True}
    else:
        parsed = {
            "sentiment_score": score,
            "sentiment_label": label,
            "drivers": drivers if drivers is not None else ["strong earnings"],
            "evidence_item_ids": evidence if evidence is not None else [],
            "self_confidence": confidence,
        }
    return ProviderResponse(provider, "fake-model", "{}", parsed, tokens - 20, 20, 5, 200)


def article_verdict(
    item_id: str,
    *,
    label: str = "BULLISH",
    score: float = 0.3,
    tickers: list[dict[str, Any]] | None = None,
    drivers: list[str] | None = None,
    rationale: str | None = "Quarterly profit rose on wider margins.",
    confidence: float = 0.8,
) -> dict[str, Any]:
    """One article's verdict, as the model would return it inside a batch."""
    return {
        "item_id": item_id,
        "sentiment_score": score,
        "sentiment_label": label,
        "rationale": rationale,
        "drivers": drivers if drivers is not None else ["laba naik"],
        "ticker_sentiments": tickers
        if tickers is not None
        else [{"ticker": "BBCA", "sentiment_score": score, "sentiment_label": label, "relevance": "PRIMARY"}],
        "self_confidence": confidence,
    }


def article_batch_response(
    verdicts: list[dict[str, Any]], *, provider: str = "gemini", tokens: int = 400
) -> ProviderResponse:
    """A batched article-sentiment response carrying one verdict per article."""
    return ProviderResponse(provider, "fake-model", "{}", {"verdicts": verdicts}, tokens - 80, 80, 5, 200)


def make_slot(provider: ScriptedProvider) -> ProviderSlot:
    """Wraps a scripted provider with a real breaker and a fast pacer."""
    return ProviderSlot(provider, CircuitBreaker(BreakerConfig(5, 120)), RatePacer(100000))


@pytest.fixture
def gold_conn() -> duckdb.DuckDBPyConnection:
    """An in-memory Gold snapshot with three securities, a split, and news."""
    con = duckdb.connect()
    con.execute(
        "create table dim_security(security_id bigint, ticker varchar, board varchar, "
        "is_fca boolean, sector_idxic varchar, special_notation varchar[], is_current boolean)"
    )
    con.execute(
        "insert into dim_security values "
        "(1001,'BBCA','MAIN',false,'FINANCIALS',[],true),"
        "(1002,'BBRI','MAIN',false,'FINANCIALS',[],true),"
        "(1003,'TLKM','MAIN',false,'INFRASTRUCTURE',[],true)"
    )
    con.execute(
        "create table fct_daily_trade(security_id bigint, trade_date date, close_adj_idr bigint, "
        "daily_return double, net_foreign_idr bigint, price_series_integrity varchar, dq_flags varchar[])"
    )
    con.execute(
        "insert into fct_daily_trade values "
        "(1001,'2026-07-03',1595,-0.80,-5000000,'CLEAN',[]),"
        "(1002,'2026-07-03',4200,-0.02,-9000000,'CLEAN',[]),"
        "(1003,'2026-07-03',3100,0.01,2000000,'CLEAN',[])"
    )
    con.execute(
        "create table fct_corporate_action(event_id varchar, security_id bigint, ticker varchar, "
        "event_type varchar, effective_trade_date date, price_adjustment_required boolean, adjustment_pending boolean)"
    )
    con.execute(
        "insert into fct_corporate_action values "
        "('ev1',1001,'BBCA','STOCK_SPLIT','2026-07-03',true,false)"
    )
    con.execute(
        "create table fct_index_level(index_id varchar, trade_date date, close_level double, change_level double)"
    )
    con.execute("insert into fct_index_level values ('IHSG','2026-07-03',7350.2,-41.3)")
    con.execute(
        "create table fct_news_item(item_id varchar, trade_date date, source varchar, lang varchar, "
        "title varchar, summary varchar, url varchar, published_at timestamp, tickers varchar[])"
    )
    con.execute(
        "insert into fct_news_item values "
        "('news:1','2026-07-03','cnbc','id','BBCA split','BBCA melakukan stock split','http://x','2026-07-03 02:00:00',['BBCA']),"
        "('news:2','2026-07-03','kontan','id','BBRI asing jual','asing net sell BBRI','http://y','2026-07-03 02:00:00',['BBRI'])"
    )
    return con


@pytest.fixture
def deps_factory(gold_conn: duckdb.DuckDBPyConnection) -> Callable[..., GraphDeps]:
    """Returns a builder for GraphDeps over the in-memory Gold, with given slots."""

    def build(slots: dict[str, ProviderSlot], tracer: Any | None = None) -> GraphDeps:
        return GraphDeps(
            slots=slots,
            pg_pool=None,
            duckdb_ro=gold_conn,
            settings=get_agentic_settings(),
            tracer=tracer or RecordingTracer(),
        )

    return build
