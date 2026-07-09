"""
route_task: the objective branch point after a cache miss.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.nodes._common import get_deps
from app.agentic.objectives import spec_for
from app.agentic.state import AgentState


async def route_task(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    async with deps.tracer.span("route_task", state["run_id"], {"objective": state["objective"]}):
        return {}


def route_objective(state: AgentState) -> str:
    return spec_for(state["objective"]).node_name
