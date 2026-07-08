"""Builds the agentic clients, provider slots, and dependency bundle once at startup.

Kept separate from the FastAPI lifespan so the same wiring backs both the running app and the
live smoke harness. Every client here is process-scoped and injected into the graph; nothing in
a node ever constructs one.
"""

from __future__ import annotations

import asyncpg
import duckdb
import httpx
from google import genai
from groq import AsyncGroq

from app.agentic.config import AgenticSettings
from app.agentic.deps import GraphDeps
from app.agentic.providers.base import ProviderSlot
from app.agentic.providers.breaker import CircuitBreaker
from app.agentic.providers.gemini import GeminiProvider
from app.agentic.providers.groq import GroqProvider
from app.agentic.providers.limits import GEMINI_LIMITS, GROQ_LIMITS, BreakerConfig
from app.agentic.providers.pacer import RatePacer
from app.agentic.tracing import NoopTracer, Tracer


def build_llm_clients(settings: AgenticSettings, http_client: httpx.AsyncClient) -> dict[str, object]:
    """Constructs the Groq and Gemini clients once, sharing the app's httpx client for Groq."""
    clients: dict[str, object] = {}
    if settings.has_groq():
        clients["groq"] = AsyncGroq(api_key=settings.groq_api_key, http_client=http_client)
    if settings.has_gemini():
        clients["gemini"] = genai.Client(api_key=settings.google_api_key)
    return clients


def build_slots(settings: AgenticSettings, clients: dict[str, object]) -> dict[str, ProviderSlot]:
    """Wraps each live client in its provider, breaker, and pacer."""
    breaker_config = BreakerConfig(
        failure_threshold=settings.breaker_failure_threshold,
        window_seconds=settings.breaker_window_seconds,
        cooldown_seconds=settings.breaker_cooldown_seconds,
    )
    slots: dict[str, ProviderSlot] = {}
    if "groq" in clients:
        groq_provider = GroqProvider(clients["groq"], settings.groq_model)  # type: ignore[arg-type]
        slots["groq"] = ProviderSlot(groq_provider, CircuitBreaker(breaker_config), RatePacer(GROQ_LIMITS.rpm))
    if "gemini" in clients:
        gemini_provider = GeminiProvider(clients["gemini"], settings.gemini_model)  # type: ignore[arg-type]
        slots["gemini"] = ProviderSlot(gemini_provider, CircuitBreaker(breaker_config), RatePacer(GEMINI_LIMITS.rpm))
    return slots


def build_deps(
    settings: AgenticSettings,
    slots: dict[str, ProviderSlot],
    pg_pool: asyncpg.Pool | None,
    duckdb_ro: duckdb.DuckDBPyConnection | None,
    tracer: Tracer | None = None,
) -> GraphDeps:
    """Assembles the injected dependency bundle the graph nodes read from."""
    return GraphDeps(
        slots=slots,
        pg_pool=pg_pool,
        duckdb_ro=duckdb_ro,
        settings=settings,
        tracer=tracer or NoopTracer(),
    )
