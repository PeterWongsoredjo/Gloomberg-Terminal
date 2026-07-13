"""
The topography of the LangGraph state machine, wires up worker nodes
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agentic.nodes.analysis import (
    after_analysis,
    deep_extract,
    sentiment_analyze,
    synthesize_insight,
)
from app.agentic.nodes.cache_lookup import cache_lookup
from app.agentic.nodes.evaluate import evaluate, route_after_evaluate
from app.agentic.nodes.finalize import finalize
from app.agentic.nodes.ingest_context import ingest_context
from app.agentic.nodes.optimize import optimize
from app.agentic.nodes.route_task import route_objective, route_task
from app.agentic.state import AgentState

_ANALYSIS_NODE_NAMES = ["sentiment_analyze", "deep_extract", "synthesize_insight"]
_ANALYSIS_NODES: dict[Hashable, str] = {name: name for name in _ANALYSIS_NODE_NAMES}


def _route_after_cache(state: AgentState) -> str:
    return "finalize" if state.get("cache_hit") else "route_task"


def build_graph(checkpointer: Any | None = None) -> Any:
    builder = StateGraph(AgentState)
    builder.add_node("ingest_context", ingest_context)
    builder.add_node("cache_lookup", cache_lookup)
    builder.add_node("route_task", route_task)
    builder.add_node("sentiment_analyze", sentiment_analyze)
    builder.add_node("deep_extract", deep_extract)
    builder.add_node("synthesize_insight", synthesize_insight)
    builder.add_node("evaluate", evaluate)
    builder.add_node("optimize", optimize)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "ingest_context")
    builder.add_edge("ingest_context", "cache_lookup")
    builder.add_conditional_edges(
        "cache_lookup", _route_after_cache, {"finalize": "finalize", "route_task": "route_task"}
    )
    builder.add_conditional_edges("route_task", route_objective, _ANALYSIS_NODES)
    for node in _ANALYSIS_NODE_NAMES:
        builder.add_conditional_edges(node, after_analysis, {"evaluate": "evaluate", "finalize": "finalize"})
    builder.add_conditional_edges(
        "evaluate", route_after_evaluate, {"optimize": "optimize", "finalize": "finalize"}
    )
    builder.add_conditional_edges("optimize", route_objective, _ANALYSIS_NODES)
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
