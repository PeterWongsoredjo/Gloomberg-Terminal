"""cache_lookup: return a fresh cached result before spending any tokens (AG-08).

A hit within the freshness window short-circuits straight to finalize, skipping inference. A
miss, a stale entry, or no ledger pool falls through to routing. Tier-2 (serving a stale entry
during an outage) lives in the analysis node, not here.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic import cache
from app.agentic.nodes._common import get_deps
from app.agentic.state import AgentState


async def cache_lookup(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Sets cache_hit and, on a fresh hit, loads the cached artifacts into state."""
    deps = get_deps(config)
    async with deps.tracer.span("cache_lookup", state["run_id"]):
        if deps.pg_pool is None:
            return {"cache_hit": False}
        hit = await cache.get(deps.pg_pool, state["working"]["cache_key"])

    ttl_seconds = deps.settings.cache_ttl_hours * 3600
    if hit is not None and hit.age_seconds <= ttl_seconds:
        return {"cache_hit": True, "artifacts": hit.artifacts}
    return {"cache_hit": False}
