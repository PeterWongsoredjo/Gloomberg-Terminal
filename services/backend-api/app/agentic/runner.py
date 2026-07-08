"""run_agentic: the async entrypoint that drives one compiled graph run to a terminal state.

This is the seam Stage 5's POST /runs endpoint will call. It seeds the CT-010 state, records the
run as RUNNING, invokes the injected compiled graph with its dependencies, and returns the final
state. Input-level idempotency comes from the AG-08 cache; run-level idempotency (the OR-04 409)
is the serving endpoint's concern.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.agentic import ledger
from app.agentic.deps import GraphDeps
from app.agentic.ids import new_ulid
from app.agentic.objectives import spec_for
from app.agentic.state import AgentState

_RECURSION_LIMIT = 60  # comfortably covers the analyze/evaluate/optimize loop under the hard cap


def _initial_state(run_id: str, objective: str, trade_date: str, universe: list[str], deps: GraphDeps) -> AgentState:
    """Seeds the CT-010 run state with the configured hard budget caps."""
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
        "trace_id": None,
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
) -> dict[str, Any]:
    """Runs the graph over a universe and returns the terminal CT-010 state."""
    run_id = run_id or new_ulid()
    trade_date_str = trade_date.isoformat() if isinstance(trade_date, date) else trade_date
    if deps.pg_pool is not None:
        await ledger.start_run(
            deps.pg_pool,
            run_id=run_id,
            objective=objective,
            trade_date=date.fromisoformat(trade_date_str),
            trace_id=None,
        )
    config = {"configurable": {"deps": deps, "thread_id": run_id}, "recursion_limit": _RECURSION_LIMIT}
    final: dict[str, Any] = await graph.ainvoke(_initial_state(run_id, objective, trade_date_str, universe, deps), config)
    return final
