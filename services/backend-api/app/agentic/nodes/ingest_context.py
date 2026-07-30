"""
ingest_context: assemble news, market context, and corporate actions for the window.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic import cache, intraday, tape
from app.agentic.deps import GraphDeps
from app.agentic.nodes._common import get_deps
from app.agentic.prompts.registry import get_prompt
from app.agentic.resolver import INDEX_SUBJECTS, EntityResolver
from app.agentic.state import AgentState
from app.agentic.warehouse import GoldReader


async def _news_for(
    deps: GraphDeps, gold: GoldReader, state: AgentState, objective: str, trade_date: str
) -> list[dict[str, Any]]:
    """Insight and scoring objectives read the poll projection, everything else reads Gold."""
    if objective not in intraday.POLLED_NEWS:
        return await gold.news_items(trade_date)
    if deps.pg_pool is None:
        return []
    if objective != "article_sentiment":
        return await intraday.day_items(deps.pg_pool, trade_date)
    return await intraday.claim_unscored_batch(
        deps.pg_pool,
        trade_date,
        deps.settings.intraday_max_score_attempts,
        deps.settings.article_items_per_poll,
        state["run_id"],
    )


async def _equity_context(
    deps: GraphDeps, gold: GoldReader, objective: str, equities: list[str], market_date: str
) -> list[dict[str, Any]]:
    """The close-of-day run reads the live tape, everything else reads the Gold snapshot."""
    if objective == "insight_synthesis" and deps.pg_pool is not None:
        rows = await tape.market_context(deps.pg_pool, equities)
        if rows:
            return rows
    return await gold.market_context(market_date, equities)


async def _market_context(
    deps: GraphDeps, gold: GoldReader, objective: str, known: list[str], market_date: str, trade_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prices and corporate actions where we have them; absence is not a veto."""
    equities = [t for t in known if t not in INDEX_SUBJECTS]
    market, corporate_actions = await asyncio.gather(
        _equity_context(deps, gold, objective, equities, market_date),
        gold.corporate_actions(equities, trade_date),
    )
    for index_id in (t for t in known if t in INDEX_SUBJECTS):
        index_row = await gold.index_context(index_id, market_date)
        if index_row is not None:
            market.append(index_row)
    return market, corporate_actions


async def ingest_context(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    gold = GoldReader(deps.duckdb_ro)
    objective = state["objective"]
    trade_date = state["trade_date"]

    async with deps.tracer.span("ingest_context", state["run_id"], {"objective": objective}):
        resolver, news = await asyncio.gather(
            EntityResolver.from_registry(deps.pg_pool, gold),
            _news_for(deps, gold, state, objective, trade_date),
        )
        known = resolver.resolve(state["subject_universe"]).resolved
        market_date = trade_date
        if objective in intraday.INTRADAY_OBJECTIVES:
            # today's Gold is unbuilt in session, anchor prices to the last snapshot
            market_date = await gold.latest_trade_date() or trade_date
        market, corporate_actions = await _market_context(
            deps, gold, objective, known, market_date, trade_date
        )

    context = {"news_items": news, "market_context": market, "corporate_actions": corporate_actions}
    prompt = get_prompt(objective)
    primary = deps.ladder_for(objective).primary_name
    cache_key = cache.compute_key(
        objective=objective,
        prompt_version=prompt.version,
        provider=primary,
        subject_universe=known,
        trade_date=trade_date,
        news_fingerprint=cache.fingerprint(news),
        market_fingerprint=cache.fingerprint(market),
    )
    return {
        "subject_universe": known,
        "context": context,
        "working": {
            "draft_artifacts": [],
            "evaluation": None,
            "active_provider": primary,
            "prompt_version": prompt.version,
            "cache_key": cache_key,
            "nothing_to_do": objective == "article_sentiment" and not news,
        },
        "status": "RUNNING",
    }
