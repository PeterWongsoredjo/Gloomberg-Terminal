"""AG-07 persistence: the ledger and checkpointer against a live Postgres (skipped if down).

Covers 04 5.7: PostgresSaver.setup provisions the checkpoint tables, finalize writes the custom
agent_run / agent_artifact ledger, and the run reaches a terminal status. Skips cleanly when the
container Postgres is not reachable so the offline suite always runs.
"""

from __future__ import annotations

import asyncpg
import duckdb
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agentic import ledger
from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.runner import run_agentic

from .conftest import RecordingTracer, ScriptedProvider, make_slot, sentiment_response


async def _pool_or_skip() -> asyncpg.Pool:
    """Opens the container Postgres pool, or skips the test when it is down."""
    settings = get_agentic_settings()
    try:
        return await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


async def test_ledger_and_checkpointer_persist(gold_conn: duckdb.DuckDBPyConnection) -> None:
    """A live run provisions checkpoints, writes the ledger, and lands an artifact."""
    settings = get_agentic_settings()
    pool = await _pool_or_skip()
    try:
        await ledger.setup(pool)
        async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
            await saver.setup()  # provisions checkpoints / checkpoint_writes / checkpoint_blobs
            slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: sentiment_response(label="BULLISH")))}
            deps = GraphDeps(slots=slots, pg_pool=pool, duckdb_ro=gold_conn, settings=settings, tracer=RecordingTracer())
            final = await run_agentic(build_graph(saver), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["TLKM"])

        assert final["status"] == "SUCCEEDED"
        run_id = final["run_id"]
        run_row = await pool.fetchrow("select status from agentic.agent_run where run_id = $1", run_id)
        assert run_row is not None and run_row["status"] == "SUCCEEDED"
        artifact_count = await pool.fetchval("select count(*) from agentic.agent_artifact where run_id = $1", run_id)
        assert artifact_count == 1
        checkpoint_tables = await pool.fetchval(
            "select count(*) from information_schema.tables where table_name = 'checkpoints'"
        )
        assert checkpoint_tables == 1
        await pool.execute("delete from agentic.agent_artifact where run_id = $1", run_id)
        await pool.execute("delete from agentic.agent_run where run_id = $1", run_id)
    finally:
        await pool.close()
