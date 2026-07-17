"""
ingest_context: assemble news, market context, and corporate actions for the window.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic import cache, intraday
from app.agentic.deps import GraphDeps
from app.agentic.nodes._common import get_deps
from app.agentic.prompts.registry import get_prompt
from app.agentic.resolver import INDEX_SUBJECTS, EntityResolver
from app.agentic.state import AgentState
from app.agentic.warehouse import GoldReader


async def _news_for(
    deps: GraphDeps, gold: GoldReader, objective: str, trade_date: str
) -> list[dict[str, Any]]:
    """Intraday objectives read the poll projection, everything else reads Gold."""
    if objective not in intraday.INTRADAY_OBJECTIVES:
        return await gold.news_items(trade_date)
    if deps.pg_pool is None:
        return []
    if objective == "intraday_insight":
        return await intraday.day_items(deps.pg_pool, trade_date)
    return await intraday.unscored_items(
        deps.pg_pool, trade_date, deps.settings.intraday_max_score_attempts
    )


async def ingest_context(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    gold = GoldReader(deps.duckdb_ro)
    objective = state["objective"]
    trade_date = state["trade_date"]

    async with deps.tracer.span("ingest_context", state["run_id"], {"objective": objective}):
        resolver, news = await asyncio.gather(
            EntityResolver.from_gold(gold), _news_for(deps, gold, objective, trade_date)
        )
        known = resolver.resolve(state["subject_universe"]).resolved
        market_date = trade_date
        if objective in intraday.INTRADAY_OBJECTIVES:
            # today's Gold is unbuilt in session, anchor prices to the last snapshot
            market_date = await gold.latest_trade_date() or trade_date
        equities = [t for t in known if t not in INDEX_SUBJECTS]
        market, corporate_actions = await asyncio.gather(
            gold.market_context(market_date, equities), gold.corporate_actions(equities, trade_date)
        )
        for index_id in (t for t in known if t in INDEX_SUBJECTS):
            index_row = await gold.index_context(index_id, market_date)
            if index_row is not None:
                market.append(index_row)
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
        },
        "status": "RUNNING",
    }
