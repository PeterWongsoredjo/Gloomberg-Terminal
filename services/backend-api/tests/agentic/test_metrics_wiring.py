"""The provider ladder and the quality gates feed Prometheus from their real code paths."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from prometheus_client import Counter

from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.providers.base import (
    ProviderRateLimited,
    ProviderRequest,
    ProviderUnavailable,
)
from app.agentic.providers.ladder import AllProvidersDown, ProviderLadder
from app.agentic.runner import run_agentic
from app.agentic.schemas import SentimentValue
from app.observability import metrics

from .conftest import ScriptedProvider, make_slot, sentiment_response


def _request() -> ProviderRequest:
    return ProviderRequest(
        objective="daily_sentiment",
        prompt_version="sent-v4",
        system="sys",
        user="user",
        response_model=SentimentValue,
    )


def _total(counter: Counter, **labels: str) -> float:
    return sum(
        sample.value
        for family in counter.collect()
        for sample in family.samples
        if sample.name.endswith("_total") and all(sample.labels.get(k) == v for k, v in labels.items())
    )


def _dead(_req: ProviderRequest) -> Any:
    raise ProviderUnavailable("503")


def _snapshot() -> dict[str, float]:
    return {
        "fallback": _total(metrics.LLM_FALLBACKS, objective="daily_sentiment", from_provider="groq",
                           to_provider="gemini", reason="error"),
        "groq_unavailable": _total(metrics.LLM_REQUESTS, provider="groq", outcome="unavailable"),
        "gemini_unavailable": _total(metrics.LLM_REQUESTS, provider="gemini", outcome="unavailable"),
        "gemini_success": _total(metrics.LLM_REQUESTS, provider="gemini", outcome="success"),
        "gemini_to_anything": _total(metrics.LLM_FALLBACKS, from_provider="gemini"),
    }


async def test_a_failed_primary_counts_one_fallback_and_both_attempts() -> None:
    before = _snapshot()
    ladder = ProviderLadder([
        make_slot(ScriptedProvider("groq", _dead)),
        make_slot(ScriptedProvider("gemini", lambda _r: sentiment_response(provider="gemini"))),
    ])

    response = await ladder.complete(_request())

    after = _snapshot()
    assert response.provider == "gemini"
    assert after["fallback"] == before["fallback"] + 1
    assert after["groq_unavailable"] == before["groq_unavailable"] + 1
    assert after["gemini_success"] == before["gemini_success"] + 1


async def test_every_provider_down_still_raises_and_is_counted() -> None:
    """Metrics must not mask the no-fabrication rule: the ladder still gives up loudly."""
    before = _snapshot()
    ladder = ProviderLadder([make_slot(ScriptedProvider("groq", _dead)), make_slot(ScriptedProvider("gemini", _dead))])

    with pytest.raises(AllProvidersDown):
        await ladder.complete(_request())

    after = _snapshot()
    assert after["groq_unavailable"] == before["groq_unavailable"] + 1
    assert after["gemini_unavailable"] == before["gemini_unavailable"] + 1
    assert after["fallback"] == before["fallback"] + 1
    assert after["gemini_to_anything"] == before["gemini_to_anything"]


async def test_an_open_breaker_is_a_fallback_without_an_attempt() -> None:
    groq = make_slot(ScriptedProvider("groq", lambda _r: sentiment_response()))
    for _ in range(5):
        groq.breaker.record_failure()
    gemini = make_slot(ScriptedProvider("gemini", lambda _r: sentiment_response(provider="gemini")))
    before_fallback = _total(metrics.LLM_FALLBACKS, from_provider="groq", reason="breaker_open")
    before_groq = _total(metrics.LLM_REQUESTS, provider="groq")

    await ProviderLadder([groq, gemini]).complete(_request())

    assert _total(metrics.LLM_FALLBACKS, from_provider="groq", reason="breaker_open") == before_fallback + 1
    assert _total(metrics.LLM_REQUESTS, provider="groq") == before_groq


async def test_rate_limits_get_their_own_outcome() -> None:
    def limited(_req: ProviderRequest) -> Any:
        raise ProviderRateLimited("429")

    before = _total(metrics.LLM_REQUESTS, provider="groq", outcome="rate_limited")
    ladder = ProviderLadder([
        make_slot(ScriptedProvider("groq", limited)),
        make_slot(ScriptedProvider("gemini", lambda _r: sentiment_response(provider="gemini"))),
    ])
    await ladder.complete(_request())
    assert _total(metrics.LLM_REQUESTS, provider="groq", outcome="rate_limited") == before + 1


async def test_the_ladder_survives_a_broken_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A metrics fault must never cost an inference."""

    class Exploding:
        def labels(self, *_: Any) -> Any:
            raise RuntimeError("registry broken")

    monkeypatch.setattr(metrics, "LLM_REQUESTS", Exploding())
    monkeypatch.setattr(metrics, "LLM_FALLBACKS", Exploding())
    ladder = ProviderLadder([
        make_slot(ScriptedProvider("groq", _dead)),
        make_slot(ScriptedProvider("gemini", lambda _r: sentiment_response(provider="gemini"))),
    ])
    assert (await ladder.complete(_request())).provider == "gemini"


async def _run(deps: GraphDeps) -> dict[str, Any]:
    return await run_agentic(
        build_graph(None), deps, objective="daily_sentiment", trade_date="2026-07-03", universe=["TLKM"]
    )


async def test_advisory_drafts_count_as_gate_rejections(deps_factory: Callable[..., GraphDeps]) -> None:
    """Every evaluate pass of a 'buy' draft is a rejection under the non_advisory gate."""
    before_rejected = _total(metrics.GATE_DRAFTS, objective="daily_sentiment", outcome="rejected")
    before_gate = _total(metrics.GATE_FAILURES, objective="daily_sentiment", gate="non_advisory")
    def responder(_req: ProviderRequest) -> Any:
        return sentiment_response(drivers=["buy this stock now"])

    final = await _run(deps_factory({"groq": make_slot(ScriptedProvider("groq", responder))}))

    assert final["status"] == "DEGRADED"
    rejected = _total(metrics.GATE_DRAFTS, objective="daily_sentiment", outcome="rejected") - before_rejected
    assert rejected >= 1
    assert _total(metrics.GATE_FAILURES, objective="daily_sentiment", gate="non_advisory") == before_gate + rejected


async def test_clean_drafts_count_as_passed(deps_factory: Callable[..., GraphDeps]) -> None:
    before = _total(metrics.GATE_DRAFTS, objective="daily_sentiment", outcome="passed")
    final = await _run(deps_factory({"groq": make_slot(ScriptedProvider("groq", lambda _r: sentiment_response()))}))
    assert final["status"] == "SUCCEEDED"
    assert _total(metrics.GATE_DRAFTS, objective="daily_sentiment", outcome="passed") == before + 1
