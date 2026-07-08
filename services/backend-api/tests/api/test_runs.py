"""SV-09 runs endpoints: the real orchestration -> agentic handoff, no MockTransport.

Drives POST /runs + GET /runs/{id} through the app with a controllable fake graph so every path
is deterministic, over a live Postgres ledger (skipped when the container is down). The graph is
faked, but the endpoint, background dispatch, idempotency registry, ledger, and CT-011 envelope
are the real ones the Prefect trigger talks to.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from app.agentic import ledger
from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.tracing import NoopTracer
from app.api.v1.deps import require_agentic
from app.core.config import settings as core_settings
from app.lifespan import AppState
from app.main import app

_OBJECTIVE = "daily_sentiment"
_TRADE_DATE = "2026-07-03"


class FakeGraph:
    """A graph whose ainvoke stamps a terminal ledger status, optionally after a gate opens."""

    def __init__(self, pool: asyncpg.Pool, *, status: str = "SUCCEEDED", gate: asyncio.Event | None = None) -> None:
        self._pool = pool
        self._status = status
        self._gate = gate

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        if self._gate is not None:
            await self._gate.wait()
        await ledger.finish_run(
            self._pool,
            run_id=state["run_id"],
            status=self._status,
            abort_reason=None,
            consumed_tokens=10,
            consumed_iterations=1,
        )
        return {**state, "status": self._status}


def _app_state(pool: asyncpg.Pool | None, graph: Any) -> AppState:
    """Builds an AppState wired to the fake graph and the live pool."""
    deps = GraphDeps(slots={}, pg_pool=pool, duckdb_ro=None, settings=get_agentic_settings(), tracer=NoopTracer())
    return AppState(compiled_graph=graph, agentic_deps=deps, pg_pool=pool)


async def _pool_or_skip() -> asyncpg.Pool:
    """Opens the container Postgres pool, or skips when it is down."""
    try:
        pool = await asyncpg.create_pool(get_agentic_settings().postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    await ledger.setup(pool)
    return pool


async def _client() -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client bound to the ASGI app; no lifespan needed since deps are overridden."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _purge(pool: asyncpg.Pool, run_id: str) -> None:
    """Removes just the run this test created, artifacts first for the FK."""
    await pool.execute("delete from agentic.agent_artifact where run_id = $1", run_id)
    await pool.execute("delete from agentic.agent_run where run_id = $1", run_id)


async def _poll_terminal(client: httpx.AsyncClient, run_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Polls GET until the run leaves RUNNING, mirroring the OR-04 poller."""
    for _ in range(100):
        resp = await client.get(f"/api/v1/runs/{run_id}", headers=headers or {})
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        if body["data"]["status"] != "RUNNING":
            return body
        await asyncio.sleep(0.05)
    raise AssertionError("run never reached a terminal status")


async def test_post_launches_and_get_polls_to_terminal() -> None:
    """The full handoff: POST returns 202 + run_id, GET walks it to a CT-011-wrapped SUCCEEDED."""
    pool = await _pool_or_skip()
    run_id = ""
    state = _app_state(pool, FakeGraph(pool))
    app.dependency_overrides[require_agentic] = lambda: state
    try:
        async for client in _client():
            resp = await client.post(
                "/api/v1/runs",
                json={"objective": _OBJECTIVE, "trade_date": _TRADE_DATE, "subject_universe": ["TLKM"]},
            )
            assert resp.status_code == 202
            run_id = resp.json()["run_id"]

            body = await _poll_terminal(client, run_id)
            assert body["data"]["status"] == "SUCCEEDED"
            assert body["freshness_slo_met"] is True
            assert body["market_state"] == "CLOSED"
            assert body["data"]["objective"] == _OBJECTIVE
            assert body["data_as_of"] is not None
    finally:
        app.dependency_overrides.clear()
        if run_id:
            await _purge(pool, run_id)
        await pool.close()


async def test_duplicate_post_is_idempotent_409() -> None:
    """A second POST for an in-flight objective:trade_date returns 409 with the same run_id."""
    pool = await _pool_or_skip()
    run_id = ""
    gate = asyncio.Event()
    state = _app_state(pool, FakeGraph(pool, gate=gate))
    app.dependency_overrides[require_agentic] = lambda: state
    try:
        async for client in _client():
            body = {"objective": _OBJECTIVE, "trade_date": _TRADE_DATE, "subject_universe": ["TLKM"]}
            first = await client.post("/api/v1/runs", json=body)
            assert first.status_code == 202
            run_id = first.json()["run_id"]

            second = await client.post("/api/v1/runs", json=body)
            assert second.status_code == 409
            assert second.json()["run_id"] == run_id

            gate.set()  # let the in-flight run finish and deregister
            terminal = await _poll_terminal(client, run_id)
            assert terminal["data"]["status"] == "SUCCEEDED"
    finally:
        app.dependency_overrides.clear()
        if run_id:
            await _purge(pool, run_id)
        await pool.close()


async def test_get_unknown_run_is_404() -> None:
    """An unknown run_id returns a problem+json 404."""
    pool = await _pool_or_skip()
    state = _app_state(pool, FakeGraph(pool))
    app.dependency_overrides[require_agentic] = lambda: state
    try:
        async for client in _client():
            resp = await client.get("/api/v1/runs/NOSUCHRUN")
            assert resp.status_code == 404
            assert resp.headers["content-type"] == "application/problem+json"
    finally:
        app.dependency_overrides.clear()
        await pool.close()


async def test_missing_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a token configured, a request without a matching Bearer is rejected before dispatch."""
    monkeypatch.setattr(core_settings, "api_token", "s3cret")
    state = _app_state(None, FakeGraph(None))
    app.dependency_overrides[require_agentic] = lambda: state
    try:
        async for client in _client():
            missing = await client.post(
                "/api/v1/runs",
                json={"objective": _OBJECTIVE, "trade_date": _TRADE_DATE, "subject_universe": ["TLKM"]},
            )
            assert missing.status_code == 401
            assert missing.headers["content-type"] == "application/problem+json"

            wrong = await client.get(
                "/api/v1/runs/whatever", headers={"Authorization": "Bearer nope"}
            )
            assert wrong.status_code == 401
    finally:
        app.dependency_overrides.clear()
