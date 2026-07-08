"""optimize: spend one loop iteration to retry with the evaluator's targeted guidance.

The evaluator's reasons already sit in working.evaluation, where the analysis node reads them
as a correction. This node's job is to charge the iteration against the hard cap, so the loop
can never exceed max_loop_iterations.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.nodes._common import get_deps
from app.agentic.state import AgentState, budget_delta


async def optimize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Charges one loop iteration; the following edge re-runs the analysis node."""
    deps = get_deps(config)
    async with deps.tracer.span("optimize", state["run_id"]):
        return {"budget": budget_delta(iterations=1)}
