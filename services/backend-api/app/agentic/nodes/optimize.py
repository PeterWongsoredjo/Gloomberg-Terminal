"""
optimize: spend one loop iteration to retry with the evaluator's targeted guidance.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.nodes._common import get_deps
from app.agentic.state import AgentState, budget_delta


async def optimize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    async with deps.tracer.span("optimize", state["run_id"]):
        return {"budget": budget_delta(iterations=1)}
