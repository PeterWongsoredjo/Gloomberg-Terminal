from __future__ import annotations

import asyncio
import contextlib
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
from app.agentic.prompts.registry import registered_templates
from app.agentic.tracing import NoopTracer
from app.core.config import settings as core_settings
from app.eval import lifecycle
from app.observability import rollup
from app.observability.config import get_observability_settings
from app.observability.cost import QuotaTracker, get_cost_model
from app.observability.heartbeat import Heartbeat
from app.observability.langfuse_tracer import LangfuseTracer, build_langfuse_client
from app.observability.slo.engine import SloEngine

logger = logging.getLogger("gloomberg.lifespan")


@dataclass
class AppState:
    duckdb_ro: duckdb.DuckDBPyConnection | None = None
    pg_pool: asyncpg.Pool | None = None
    llm_clients: dict[str, Any] | None = None
    langfuse_handler: Any | None = None
    compiled_graph: Any | None = None
    agentic_deps: GraphDeps | None = None
    slo_engine: Any | None = None
    heartbeat: Any | None = None
    run_registry: dict[str, str] = field(default_factory=dict)
    run_tasks: set[asyncio.Task[None]] = field(default_factory=set)


async def _open_pg_pool(settings: AgenticSettings) -> asyncpg.Pool | None:
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
    try:
        return duckdb.connect(core_settings.duckdb_gold_path, read_only=True)
    except (duckdb.Error, OSError) as exc:
        logger.warning("gold snapshot unavailable, market context empty: %s", exc)
        return None


async def _open_checkpointer(stack: AsyncExitStack, settings: AgenticSettings, pg_up: bool) -> Any | None:
    if not pg_up:
        return None
    try:
        saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(settings.postgres_dsn))
        await saver.setup()
        return saver
    except Exception as exc:  # checkpointing is best-effort; a down store must not block boot
        logger.warning("checkpointer unavailable, runs will not be resumable: %s", exc)
        return None


async def _cancel_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _seed_quota(pool: asyncpg.Pool, quota: QuotaTracker) -> None:
    for provider, (requests, tokens) in (await ledger.read_provider_consumption(pool)).items():
        quota.seed(provider, requests=requests, tokens=tokens)


async def _reconcile_prompts(langfuse: LangfuseTracer) -> None:
    from app.observability import operator_log

    templates = registered_templates()
    # the per-template checks are independent network calls, so run them concurrently off-loop
    results = await asyncio.gather(*(asyncio.to_thread(langfuse.reconcile_prompt, t) for t in templates))
    for template, matched in zip(templates, results, strict=True):
        if not matched:
            operator_log.log_alert(
                {"alert_id": "prompt_registry_drift", "severity": "WARN", "payload": {"prompt": template.prompt_id}}
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """Owns every long-lived resource; the agentic graph is compiled once here."""
    settings = get_agentic_settings()
    obs_settings = get_observability_settings()
    async with AsyncExitStack() as stack:
        http_client = await stack.enter_async_context(httpx.AsyncClient(timeout=30.0))
        pg_pool = await _open_pg_pool(settings)
        quota = QuotaTracker(get_cost_model(), threshold=obs_settings.quota_shift_threshold)
        if pg_pool is not None:
            stack.push_async_callback(pg_pool.close)
            await ledger.setup(pg_pool)
            await rollup.setup(pg_pool)
            await lifecycle.setup(pg_pool)
            await _seed_quota(pg_pool, quota)
        duckdb_ro = _open_duckdb()
        if duckdb_ro is not None:
            stack.callback(duckdb_ro.close)

        clients = build_llm_clients(settings, http_client)
        slots = build_slots(settings, clients)
        checkpointer = await _open_checkpointer(stack, settings, pg_pool is not None)
        langfuse = LangfuseTracer(build_langfuse_client(obs_settings))
        stack.callback(langfuse.shutdown)
        deps = build_deps(settings, slots, pg_pool, duckdb_ro, NoopTracer(), quota)
        graph = build_graph(checkpointer)

        heartbeat = Heartbeat(
            interval_seconds=obs_settings.heartbeat_interval_seconds,
            stale_after_seconds=obs_settings.heartbeat_stale_after_seconds,
        )
        hb_task = asyncio.create_task(heartbeat.run())
        stack.push_async_callback(_cancel_task, hb_task)
        if langfuse.enabled:
            reconcile_task = asyncio.create_task(_reconcile_prompts(langfuse))
            stack.push_async_callback(_cancel_task, reconcile_task)

        logger.info(
            "agentic+observability up: providers=%s checkpointer=%s langfuse=%s",
            list(slots), checkpointer is not None, langfuse.enabled,
        )
        yield {
            "app_state": AppState(
                duckdb_ro=duckdb_ro,
                pg_pool=pg_pool,
                llm_clients=clients,
                langfuse_handler=langfuse,
                compiled_graph=graph,
                agentic_deps=deps,
                slo_engine=SloEngine(),
                heartbeat=heartbeat,
            )
        }
