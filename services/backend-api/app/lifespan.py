"""SV-01 lifespan: build every long-lived resource once, inject it, tear it down.

Stage 3 fills the agentic slots here: the Groq and Gemini clients, the compiled graph with its
Postgres checkpointer, and the injected dependency bundle the graph runs on. Infra is opened
best-effort, so the app still starts when MinIO/Postgres/Gold are down and the graph degrades
visibly instead of the process failing to boot.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import duckdb
import httpx
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agentic import ledger
from app.agentic.bootstrap import build_deps, build_llm_clients, build_slots
from app.agentic.config import AgenticSettings, get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.tracing import NoopTracer
from app.core.config import settings as core_settings

logger = logging.getLogger("gloomberg.lifespan")


@dataclass
class AppState:
    """Process-scoped resources built once at startup (SV-01), never per-request."""

    duckdb_ro: duckdb.DuckDBPyConnection | None = None  # read-only attach to the published Gold snapshot
    pg_pool: asyncpg.Pool | None = None  # async Postgres pool for agg_* + ledger reads
    llm_clients: dict[str, Any] | None = None  # groq + gemini async clients
    langfuse_handler: Any | None = None  # trace sink; a no-op tracer until Stage 4 wires Langfuse
    compiled_graph: Any | None = None  # compiled LangGraph, clients injected at compile time
    agentic_deps: GraphDeps | None = None  # injected bundle the graph nodes read
    slo_engine: Any | None = None  # freshness evaluator backing CT-011.freshness_slo_met
    run_registry: dict[str, str] = field(default_factory=dict)  # idempotency_key -> in-flight run_id
    run_tasks: set[asyncio.Task[None]] = field(default_factory=set)  # keeps background runs from being GC'd


async def _open_pg_pool(settings: AgenticSettings) -> asyncpg.Pool | None:
    """Opens the Postgres pool, or returns None when Postgres is unreachable."""
    try:
        return await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=core_settings.pg_pool_min_size,
            max_size=core_settings.pg_pool_max_size,
        )
    except (OSError, asyncpg.PostgresError) as exc:
        logger.warning("postgres pool unavailable, ledger and cache disabled: %s", exc)
        return None


def _open_duckdb() -> duckdb.DuckDBPyConnection | None:
    """Opens the published Gold snapshot read-only, or None when it is absent."""
    try:
        return duckdb.connect(core_settings.duckdb_gold_path, read_only=True)
    except (duckdb.Error, OSError) as exc:
        logger.warning("gold snapshot unavailable, market context empty: %s", exc)
        return None


async def _open_checkpointer(stack: AsyncExitStack, settings: AgenticSettings, pg_up: bool) -> Any | None:
    """Provisions the AG-07 Postgres checkpointer when Postgres is up, else None."""
    if not pg_up:
        return None
    try:
        saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(settings.postgres_dsn))
        await saver.setup()
        return saver
    except Exception as exc:  # checkpointing is best-effort; a down store must not block boot
        logger.warning("checkpointer unavailable, runs will not be resumable: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """Owns every long-lived resource; the agentic graph is compiled once here."""
    settings = get_agentic_settings()
    async with AsyncExitStack() as stack:
        http_client = await stack.enter_async_context(httpx.AsyncClient(timeout=30.0))
        pg_pool = await _open_pg_pool(settings)
        if pg_pool is not None:
            stack.push_async_callback(pg_pool.close)
            await ledger.setup(pg_pool)
        duckdb_ro = _open_duckdb()
        if duckdb_ro is not None:
            stack.callback(duckdb_ro.close)

        clients = build_llm_clients(settings, http_client)
        slots = build_slots(settings, clients)
        checkpointer = await _open_checkpointer(stack, settings, pg_pool is not None)
        tracer = NoopTracer()
        deps = build_deps(settings, slots, pg_pool, duckdb_ro, tracer)
        graph = build_graph(checkpointer)

        logger.info("agentic layer up: providers=%s checkpointer=%s", list(slots), checkpointer is not None)
        yield {
            "app_state": AppState(
                duckdb_ro=duckdb_ro,
                pg_pool=pg_pool,
                llm_clients=clients,
                langfuse_handler=tracer,
                compiled_graph=graph,
                agentic_deps=deps,
            )
        }
