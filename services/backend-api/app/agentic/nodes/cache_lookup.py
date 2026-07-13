"""
return a fresh cached result before spending any tokens.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic import cache
from app.agentic.nodes._common import get_deps
from app.agentic.state import AgentState


async def cache_lookup(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    async with deps.tracer.span("cache_lookup", state["run_id"]):
        if deps.pg_pool is None:
            return {"cache_hit": False}
        hit = await cache.get(deps.pg_pool, state["working"]["cache_key"])

    ttl_seconds = deps.settings.cache_ttl_hours * 3600
    if hit is not None and hit.age_seconds <= ttl_seconds:
        return {"cache_hit": True, "artifacts": hit.artifacts}
    return {"cache_hit": False}
