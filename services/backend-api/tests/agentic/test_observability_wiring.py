"""Stage 4 wiring: the Langfuse trace id reaches artifacts, quota is tracked, None changes nothing.

These prove the observability wiring is an injection: the full LangGraph run drives to a terminal
state with a tracer, without one, and stamps the minted trace id into CT-009 provenance either way.
"""

from __future__ import annotations

import asyncio
from datetime import date

import duckdb

from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.providers.base import ProviderSlot
from app.agentic.runner import run_agentic
from app.agentic.tracing import NoopTracer
from app.observability.cost import QuotaTracker, get_cost_model
from app.observability.langfuse_tracer import LangfuseTracer
from tests.agentic.conftest import ScriptedProvider, make_slot, sentiment_response


class _StampTracer(LangfuseTracer):
    """A tracer that hands back a fixed trace id without touching the network."""

    def __init__(self, trace_id: str) -> None:
        super().__init__(client=object())
        self._trace_id = trace_id
        self.flushed = False

    def new_trace_id(self) -> str:
        return self._trace_id

    def callbacks_for(self, trace_id: str | None) -> list[object]:
        return []

    def flush(self) -> None:
        self.flushed = True


def _deps(gold_conn: duckdb.DuckDBPyConnection, slots: dict[str, ProviderSlot]) -> GraphDeps:
    return GraphDeps(
        slots=slots,
        pg_pool=None,
        duckdb_ro=gold_conn,
        settings=get_agentic_settings(),
        tracer=NoopTracer(),
        quota=QuotaTracker(get_cost_model(), threshold=0.95, today=lambda: date(2026, 7, 3)),
    )


def test_run_stamps_trace_id_into_artifacts(gold_conn: duckdb.DuckDBPyConnection) -> None:
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda r: sentiment_response(provider="groq")))}
    deps = _deps(gold_conn, slots)
    tracer = _StampTracer("trace-abc123")
    final = asyncio.run(
        run_agentic(
            build_graph(), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["BBCA"], tracer=tracer
        )
    )
    assert final["status"] == "SUCCEEDED"
    assert final["artifacts"][0]["provenance"]["trace_id"] == "trace-abc123"
    assert tracer.flushed is True


def test_run_records_provider_quota(gold_conn: duckdb.DuckDBPyConnection) -> None:
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda r: sentiment_response(provider="groq")))}
    deps = _deps(gold_conn, slots)
    asyncio.run(
        run_agentic(build_graph(), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["BBCA"])
    )
    assert deps.quota is not None
    requests, tokens = deps.quota.consumption("groq")
    assert requests >= 1 and tokens > 0


def test_run_completes_without_a_tracer(gold_conn: duckdb.DuckDBPyConnection) -> None:
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda r: sentiment_response(provider="groq")))}
    deps = _deps(gold_conn, slots)
    final = asyncio.run(
        run_agentic(build_graph(), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["BBCA"])
    )
    assert final["status"] == "SUCCEEDED"
    assert final["artifacts"][0]["provenance"]["trace_id"] is None
