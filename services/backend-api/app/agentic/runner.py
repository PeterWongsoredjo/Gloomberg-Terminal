"""runner here launches the run with AgentState, it:
1. sets up the AgentState (token budgets, iteration counts, etc.)
2. calls the graph to run the analyze/evaluate/optimize loop
3. logs the results to the ledger
4. handles failures and manages provider health
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.agentic import ledger
from app.agentic.deps import GraphDeps
from app.agentic.ids import new_ulid
from app.agentic.objectives import spec_for
from app.agentic.state import AgentState
from app.observability.langfuse_tracer import LangfuseTracer

_RECURSION_LIMIT = 60  # analyze/evaluate/optimize loop hard cap


def _initial_state(
    run_id: str, objective: str, trade_date: str, universe: list[str], deps: GraphDeps, trace_id: str | None
) -> AgentState:
    return {
        "run_id": run_id,
        "objective": objective,  # type: ignore[typeddict-item]
        "subject_universe": universe,
        "trade_date": trade_date,
        "budget": {
            "max_loop_iterations": deps.settings.max_loop_iterations,
            "max_total_tokens": deps.settings.max_total_tokens,
            "consumed_tokens": 0,
            "consumed_iterations": 0,
        },
        "provider_ladder": [*spec_for(objective).ladder, "cache_only"],
        "artifacts": [],
        "status": "RUNNING",
        "abort_reason": None,
        "trace_id": trace_id,
        "cache_hit": False,
    }


async def run_agentic(
    graph: Any,
    deps: GraphDeps,
    *,
    objective: str,
    trade_date: date | str,
    universe: list[str],
    run_id: str | None = None,
    tracer: LangfuseTracer | None = None,
) -> dict[str, Any]:
    """the graph invoke starts here, take the initial state,
    packages it with GraphDeps and hands it to LangGraph
    then logs everything that happens and manages provider health
    """
    run_id = run_id or new_ulid()
    trade_date_str = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
    trace_id = tracer.new_trace_id() if tracer is not None else None
    if deps.pg_pool is not None:
        await ledger.start_run(
            deps.pg_pool,
            run_id=run_id,
            objective=objective,
            trade_date=date.fromisoformat(trade_date_str),
            trace_id=trace_id,
        )
    config: dict[str, Any] = {"configurable": {"deps": deps, "thread_id": run_id}, "recursion_limit": _RECURSION_LIMIT}
    if tracer is not None:
        callbacks = tracer.callbacks_for(trace_id)
        if callbacks:
            config["callbacks"] = callbacks
    try:
        final: dict[str, Any] = await graph.ainvoke(
            _initial_state(run_id, objective, trade_date_str, universe, deps, trace_id), config
        )
    finally:
        if tracer is not None:
            await asyncio.to_thread(tracer.flush)  # flush off the event loop; a slow Langfuse never blocks it
    return final
