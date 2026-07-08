"""ingest_context: assemble news, market context, and corporate actions for the window.

The subject universe is validated against dim_security here, so a code that is not a current
IDX security never reaches an analysis node. Independent Gold reads run concurrently, and the
AG-08 cache key is computed once here since every input it hashes is now known.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic import cache
from app.agentic.nodes._common import get_deps
from app.agentic.prompts.registry import get_prompt
from app.agentic.resolver import EntityResolver
from app.agentic.state import AgentState
from app.agentic.warehouse import GoldReader


async def ingest_context(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Builds the shared context and prunes the universe to known securities."""
    deps = get_deps(config)
    gold = GoldReader(deps.duckdb_ro)
    objective = state["objective"]
    trade_date = state["trade_date"]

    async with deps.tracer.span("ingest_context", state["run_id"], {"objective": objective}):
        resolver, news = await asyncio.gather(
            EntityResolver.from_gold(gold), gold.news_items(trade_date)
        )
        known = resolver.resolve(state["subject_universe"]).resolved
        market, corporate_actions = await asyncio.gather(
            gold.market_context(trade_date, known), gold.corporate_actions(known, trade_date)
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
        },
        "status": "RUNNING",
    }
