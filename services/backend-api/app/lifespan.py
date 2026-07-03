from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg
import duckdb
from fastapi import FastAPI


@dataclass
class AppState:
    """Process-scoped resources built once at startup (SV-01), never per-request."""

    duckdb_ro: duckdb.DuckDBPyConnection | None = None  # read-only attach to the published Gold snapshot
    pg_pool: asyncpg.Pool | None = None  # async Postgres pool for agg_* + ledger reads
    llm_clients: dict[str, Any] | None = None  # groq + gemini async clients
    langfuse_handler: Any | None = None  # LLM trace callback handler
    compiled_graph: Any | None = None  # compiled LangGraph, clients injected at compile time
    slo_engine: Any | None = None  # freshness evaluator backing CT-011.freshness_slo_met


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, AppState]]:
    """Owns every long-lived resource, each field is wired by the stage that needs it."""
    yield {"app_state": AppState()}
