"""Bootstrapping here means:
1. Preparing the dependencies Bundle (deps.py)
2. Initializing LLM Clients (Gemini, Groq)
3. wrapping each in a circuit breaker, rate pacer, quota guard, based in (limits.py)
"""

from __future__ import annotations

import asyncpg
import duckdb
import httpx
from google import genai
from groq import AsyncGroq

from app.agentic.config import AgenticSettings
from app.agentic.deps import GraphDeps
from app.agentic.providers.base import ProviderSlot, QuotaGuard
from app.agentic.providers.breaker import CircuitBreaker
from app.agentic.providers.gemini import GeminiProvider
from app.agentic.providers.groq import GroqProvider
from app.agentic.providers.limits import GEMINI_LIMITS, GROQ_LIMITS, BreakerConfig
from app.agentic.providers.pacer import RatePacer
from app.agentic.tracing import NoopTracer, Tracer


def build_llm_clients(settings: AgenticSettings, http_client: httpx.AsyncClient) -> dict[str, object]:
    """looks up .env API keys and opens the raw connection for Gemini and Groq"""
    clients: dict[str, object] = {}
    if settings.has_groq():
        clients["groq"] = AsyncGroq(api_key=settings.groq_api_key, http_client=http_client)
    if settings.has_gemini():
        clients["gemini"] = genai.Client(api_key=settings.google_api_key)
    return clients


def build_slots(settings: AgenticSettings, clients: dict[str, object]) -> dict[str, ProviderSlot]:
    """wraps the LLM clients in a ProviderSlot (point 3)"""
    breaker_config = BreakerConfig(
        failure_threshold=settings.breaker_failure_threshold,
        window_seconds=settings.breaker_window_seconds,
        cooldown_seconds=settings.breaker_cooldown_seconds,
    )
    slots: dict[str, ProviderSlot] = {}
    if "groq" in clients:
        groq_provider = GroqProvider(clients["groq"], settings.groq_model)  # type: ignore[arg-type]
        slots["groq"] = ProviderSlot(
            groq_provider,
            CircuitBreaker(breaker_config),
            RatePacer(GROQ_LIMITS.rpm, GROQ_LIMITS.tpm),
        )
    if "gemini" in clients:
        gemini_provider = GeminiProvider(clients["gemini"], settings.gemini_model)  # type: ignore[arg-type]
        slots["gemini"] = ProviderSlot(
            gemini_provider,
            CircuitBreaker(breaker_config),
            RatePacer(GEMINI_LIMITS.rpm, GEMINI_LIMITS.tpm),
        )
    return slots


def build_deps(
    settings: AgenticSettings,
    slots: dict[str, ProviderSlot],
    pg_pool: asyncpg.Pool | None,
    duckdb_ro: duckdb.DuckDBPyConnection | None,
    tracer: Tracer | None = None,
    quota: QuotaGuard | None = None,
) -> GraphDeps:
    """never connect the graph to db and LLM Clients, only inject the graph deps (point 1)"""
    return GraphDeps(
        slots=slots,
        pg_pool=pg_pool,
        duckdb_ro=duckdb_ro,
        settings=settings,
        tracer=tracer or NoopTracer(),
        quota=quota,
    )
