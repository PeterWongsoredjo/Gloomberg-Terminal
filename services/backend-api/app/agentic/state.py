"""AG-01 LangGraph shared state: the CT-010 envelope plus node-level working fields.

Accumulator channels carry reducers so parallel or looped supersteps combine instead of
clobbering: artifacts append, token and iteration counters add. Everything else replaces.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Objective = Literal["daily_sentiment", "deep_extraction", "insight_synthesis"]
RunStatus = Literal["RUNNING", "SUCCEEDED", "DEGRADED", "ABORTED"]


class Budget(TypedDict):
    """CT-010 hard caps and running consumption; the runaway guard (ADR-002)."""

    max_loop_iterations: int
    max_total_tokens: int
    consumed_tokens: int
    consumed_iterations: int


def merge_budget(current: Budget, update: Budget) -> Budget:
    """Keeps the caps and adds the consumed deltas; the empty first write seeds the caps."""
    if not current:
        return update
    return {
        "max_loop_iterations": current["max_loop_iterations"] or update["max_loop_iterations"],
        "max_total_tokens": current["max_total_tokens"] or update["max_total_tokens"],
        "consumed_tokens": current["consumed_tokens"] + update["consumed_tokens"],
        "consumed_iterations": current["consumed_iterations"] + update["consumed_iterations"],
    }


class Context(TypedDict):
    """Everything the analysis nodes read, assembled once by ingest_context."""

    news_items: list[dict[str, Any]]
    market_context: list[dict[str, Any]]
    corporate_actions: list[dict[str, Any]]


class Working(TypedDict):
    """Per-iteration scratch; replaced each superstep, never accumulated."""

    draft_artifacts: list[dict[str, Any]]
    evaluation: dict[str, Any] | None
    active_provider: str
    prompt_version: str
    cache_key: str  # AG-08 key, computed once in ingest_context and reused


class AgentState(TypedDict, total=False):
    """The full graph state persisted at each superstep boundary (CT-010 / AG-01)."""

    run_id: str
    objective: Objective
    subject_universe: list[str]
    trade_date: str
    budget: Annotated[Budget, merge_budget]
    provider_ladder: list[str]
    context: Context
    working: Working
    artifacts: Annotated[list[dict[str, Any]], operator.add]
    cache_hit: bool
    status: RunStatus
    abort_reason: str | None
    trace_id: str | None


def budget_delta(*, tokens: int = 0, iterations: int = 0) -> Budget:
    """A budget update carrying only the consumed deltas for this superstep."""
    return {
        "max_loop_iterations": 0,
        "max_total_tokens": 0,
        "consumed_tokens": tokens,
        "consumed_iterations": iterations,
    }
