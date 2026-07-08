"""A gated live smoke against real Groq + the published Gold, to prove the adapters work.

Skipped unless a Groq key is present and a Gold snapshot has been published. It exercises the
real provider adapter, the ladder, and the graph end to end; the result is non-deterministic, so
it only asserts a valid terminal state and, on success, well-formed CT-009 artifacts.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import httpx
import pytest

from app.agentic.bootstrap import build_deps, build_llm_clients, build_slots
from app.agentic.config import get_agentic_settings
from app.agentic.graph import build_graph
from app.agentic.runner import run_agentic
from app.core.config import settings as core_settings


async def test_live_groq_smoke() -> None:
    """Runs daily_sentiment against live Groq over a real universe from published Gold."""
    settings = get_agentic_settings()
    if not settings.has_groq():
        pytest.skip("no Groq key present")
    gold_path = Path(core_settings.duckdb_gold_path)
    if not gold_path.exists():
        pytest.skip("no published Gold snapshot")

    gold = duckdb.connect(str(gold_path), read_only=True)
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        clients = build_llm_clients(settings, http_client)
        deps = build_deps(settings, build_slots(settings, clients), None, gold)
        final = await run_agentic(
            build_graph(None), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["BBCA", "BBRI"]
        )

    assert final["status"] in {"SUCCEEDED", "DEGRADED"}
    for artifact in final["artifacts"]:
        assert artifact["artifact_type"] == "SENTIMENT"
        assert -1.0 <= artifact["value"]["sentiment_score"] <= 1.0
        assert artifact["provenance"]["provider"] in {"groq", "gemini"}
