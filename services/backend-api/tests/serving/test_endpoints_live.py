"""End-to-end endpoint checks against real Postgres projections and the Gold snapshot.

Skipped cleanly when the container Postgres is down or the serving projections have not been
synced, so the suite stays green on a cold box. When infra is up these prove every payload is
envelope-wrapped and the tape stream opens with a snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg
import duckdb
import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.agentic.config import get_agentic_settings
from app.api.v1 import tape
from app.api.v1.deps import get_app_state
from app.core.config import settings as core_settings
from app.lifespan import AppState
from app.main import app
from app.observability.slo.engine import SloEngine

_ENVELOPE_KEYS = {"api_version", "served_at", "data_as_of", "freshness_slo_met", "market_state", "quality_flags", "data"}


async def _state_or_skip() -> AppState:
    """Builds an AppState on real infra, or skips when Postgres or the projections are absent."""
    gold_path = Path(core_settings.duckdb_gold_path)
    if not gold_path.exists():
        pytest.skip("gold snapshot not published")
    try:
        pool = await asyncpg.create_pool(get_agentic_settings().postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    try:
        await pool.fetchval('select 1 from public."agg_live_tape" limit 1')
    except asyncpg.PostgresError:
        await pool.close()
        pytest.skip("serving projections not synced to postgres")
    duckdb_ro = duckdb.connect(str(gold_path), read_only=True)
    return AppState(duckdb_ro=duckdb_ro, pg_pool=pool, slo_engine=SloEngine())


async def _client(state: AppState) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_app_state] = lambda: state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_app_state, None)


def _assert_enveloped(body: dict[str, Any]) -> None:
    assert _ENVELOPE_KEYS <= set(body)
    assert body["data_as_of"] is not None


async def test_core_get_endpoints_are_enveloped() -> None:
    """market/state, tape, securities, sentiment, and news each return a valid envelope."""
    state = await _state_or_skip()
    try:
        async for client in _client(state):
            for path in ("/api/v1/market/state", "/api/v1/tape", "/api/v1/securities?limit=5",
                         "/api/v1/sentiment/matrix", "/api/v1/news?limit=5"):
                resp = await client.get(path)
                assert resp.status_code == 200, f"{path} -> {resp.status_code}"
                _assert_enveloped(resp.json())
    finally:
        await _close(state)


async def test_securities_pagination_walks() -> None:
    """A page returns at most the limit and its cursor fetches a disjoint next page."""
    state = await _state_or_skip()
    try:
        async for client in _client(state):
            first = (await client.get("/api/v1/securities?limit=3")).json()
            assert len(first["data"]["rows"]) <= 3
            cursor = first["data"]["next_cursor"]
            if cursor:
                second = (await client.get(f"/api/v1/securities?limit=3&cursor={cursor}")).json()
                first_ids = {r["security_id"] for r in first["data"]["rows"]}
                second_ids = {r["security_id"] for r in second["data"]["rows"]}
                assert first_ids.isdisjoint(second_ids)
    finally:
        await _close(state)


def test_tape_websocket_opens_with_snapshot() -> None:
    """Connecting to the tape stream yields a snapshot frame first.

    Synchronous so TestClient owns the event loop; the pool is opened inside that loop by the app's
    own lifespan, since an asyncpg pool is bound to the loop that created it.
    """
    if not asyncio.run(_infra_ready()):
        pytest.skip("postgres/projections/gold not available")

    ws_app = FastAPI(lifespan=_ws_lifespan)
    ws_app.include_router(tape.router, prefix="/api/v1")
    with TestClient(ws_app) as client, client.websocket_connect("/api/v1/tape/stream") as socket:
        frame = socket.receive_json()
        assert frame["type"] == "snapshot"
        assert "envelope" in frame
        socket.close()


def test_tape_websocket_is_public_when_token_gate_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tape stream stays a public read even with the operator token configured."""
    if not asyncio.run(_infra_ready()):
        pytest.skip("postgres/projections/gold not available")

    monkeypatch.setattr(core_settings, "api_token", "operator-secret")
    ws_app = FastAPI(lifespan=_ws_lifespan)
    ws_app.include_router(tape.router, prefix="/api/v1")
    with TestClient(ws_app) as client, client.websocket_connect("/api/v1/tape/stream") as socket:
        assert socket.receive_json()["type"] == "snapshot"
        socket.close()


async def _infra_ready() -> bool:
    """Probes gold + postgres + the synced projection, closing the probe pool it opens."""
    if not Path(core_settings.duckdb_gold_path).exists():
        return False
    try:
        pool = await asyncpg.create_pool(get_agentic_settings().postgres_dsn, min_size=1, max_size=1)
    except (OSError, asyncpg.PostgresError):
        return False
    try:
        await pool.fetchval('select 1 from public."agg_live_tape" limit 1')
        return True
    except asyncpg.PostgresError:
        return False
    finally:
        await pool.close()


@asynccontextmanager
async def _ws_lifespan(_app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """Opens the pool and gold connection in TestClient's own loop, for the websocket handler."""
    pool = await asyncpg.create_pool(get_agentic_settings().postgres_dsn, min_size=1, max_size=2)
    duckdb_ro = duckdb.connect(str(Path(core_settings.duckdb_gold_path)), read_only=True)
    try:
        yield {"app_state": AppState(duckdb_ro=duckdb_ro, pg_pool=pool, slo_engine=SloEngine())}
    finally:
        await pool.close()
        duckdb_ro.close()


async def _close(state: AppState) -> None:
    if state.duckdb_ro is not None:
        state.duckdb_ro.close()
    if state.pg_pool is not None:
        await state.pg_pool.close()
