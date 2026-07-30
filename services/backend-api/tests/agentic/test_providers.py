"""AG-05/AG-06 provider tests: breaker, pacer, ladder substitution, adapter enforcement."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agentic.providers.base import (
    ProviderRateLimited,
    ProviderRequest,
    ProviderUnavailable,
)
from app.agentic.providers.breaker import CircuitBreaker
from app.agentic.providers.gemini import GeminiProvider, _supported_schema
from app.agentic.providers.groq import GroqProvider
from app.agentic.providers.ladder import AllProvidersDown, ProviderLadder
from app.agentic.providers.limits import BreakerConfig
from app.agentic.providers.pacer import RatePacer
from app.agentic.schemas import ArticleSentimentBatch, SentimentValue

from .conftest import ScriptedProvider, make_slot, sentiment_response


def _request() -> ProviderRequest:
    return ProviderRequest(
        objective="daily_sentiment",
        prompt_version="sent-v4",
        system="sys",
        user="user",
        response_model=SentimentValue,
    )


def test_breaker_trips_after_threshold_and_recovers() -> None:
    """The breaker opens at the threshold, then half-opens after cooldown and closes."""
    clock = {"t": 0.0}
    breaker = CircuitBreaker(BreakerConfig(3, 60, 120), clock=lambda: clock["t"])
    for _ in range(3):
        breaker.record_failure()
    assert str(breaker.state) == "OPEN"
    assert not breaker.allow()
    clock["t"] = 121.0
    assert str(breaker.state) == "HALF_OPEN"
    assert breaker.allow()
    breaker.record_success()
    assert str(breaker.state) == "CLOSED"


def test_breaker_reopens_on_half_open_failure() -> None:
    """A failed half-open probe re-opens the breaker immediately."""
    clock = {"t": 0.0}
    breaker = CircuitBreaker(BreakerConfig(2, 60, 60), clock=lambda: clock["t"])
    breaker.record_failure()
    breaker.record_failure()
    clock["t"] = 61.0
    assert str(breaker.state) == "HALF_OPEN"
    breaker.record_failure()
    assert str(breaker.state) == "OPEN"


async def test_pacer_blocks_when_bucket_empty() -> None:
    """The pacer sleeps for the refill deficit once its bucket is drained."""
    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    pacer = RatePacer(60, clock=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(60):
        await pacer.acquire()
    await pacer.acquire()
    assert slept and slept[0] > 0


def _fake_clock() -> tuple[dict[str, float], list[float], Any]:
    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    return clock, slept, fake_sleep


async def test_pacer_without_a_token_ceiling_is_unchanged() -> None:
    """Omitting tpm has to behave exactly as the rpm-only pacer always did."""
    clock, slept, fake_sleep = _fake_clock()
    pacer = RatePacer(60, clock=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(60):
        await pacer.acquire(999_999)
    assert not slept
    await pacer.acquire(999_999)
    assert slept and slept[0] > 0


async def test_pacer_throttles_on_tokens_before_requests() -> None:
    """Groq allows 30 requests a minute but only 6000 tokens, so batches hit tpm first."""
    clock, slept, fake_sleep = _fake_clock()
    pacer = RatePacer(30, 6000, clock=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(4):
        await pacer.acquire(3800)
    assert sum(slept) > 60.0


async def test_a_roomy_token_ceiling_leaves_requests_binding() -> None:
    """Gemini's 250k tpm means rpm stays the only limit, even for batches."""
    clock, slept, fake_sleep = _fake_clock()
    pacer = RatePacer(15, 250_000, clock=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(15):
        await pacer.acquire(3800)
    assert not slept


async def test_a_request_larger_than_the_bucket_still_completes() -> None:
    """An oversized request must be capped, not deadlock the run forever."""
    clock, slept, fake_sleep = _fake_clock()
    pacer = RatePacer(30, 6000, clock=lambda: clock["t"], sleep=fake_sleep)
    await pacer.acquire(50_000)


async def test_ladder_substitutes_past_rate_limit() -> None:
    """A 429 on the primary moves to the next provider and records the failure."""

    def groq_429(_req: ProviderRequest) -> Any:
        raise ProviderRateLimited("429")

    groq = make_slot(ScriptedProvider("groq", groq_429))
    gemini = make_slot(ScriptedProvider("gemini", lambda _r: sentiment_response(provider="gemini")))
    ladder = ProviderLadder([groq, gemini])

    response = await ladder.complete(_request())
    assert response.provider == "gemini"
    assert groq.breaker.failure_count == 1


async def test_ladder_all_down_raises() -> None:
    """When every provider is unavailable the ladder raises AllProvidersDown."""

    def dead(_req: ProviderRequest) -> Any:
        raise ProviderUnavailable("503")

    ladder = ProviderLadder([make_slot(ScriptedProvider("groq", dead)), make_slot(ScriptedProvider("gemini", dead))])
    with pytest.raises(AllProvidersDown):
        await ladder.complete(_request())


async def test_groq_adapter_uses_json_object_mode() -> None:
    """The Groq adapter requests JSON object mode with the pinned decoding params."""
    captured: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        message = SimpleNamespace(content='{"sentiment_score":0.1,"sentiment_label":"NEUTRAL","drivers":[],"evidence_item_ids":[],"self_confidence":0.5}')
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = GroqProvider(client, "llama-3.3-70b-versatile")  # type: ignore[arg-type]
    response = await provider.complete(_request())
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["seed"] == 42
    assert response.parsed is not None and response.parsed["sentiment_label"] == "NEUTRAL"


async def test_gemini_adapter_passes_response_schema() -> None:
    """The Gemini adapter constrains generation with the raw AG-02 JSON schema."""
    captured: dict[str, Any] = {}

    async def generate_content(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"sentiment_score":-0.2,"sentiment_label":"BEARISH","drivers":[],"evidence_item_ids":[],"self_confidence":0.6}',
            usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=6),
        )

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    provider = GeminiProvider(client, "gemini-3.1-flash-lite")  # type: ignore[arg-type]
    response = await provider.complete(_request())
    sent = captured["config"].response_json_schema
    assert sent["properties"]["sentiment_label"] == SentimentValue.model_json_schema()["properties"]["sentiment_label"]
    assert captured["config"].response_mime_type == "application/json"
    assert response.parsed is not None and response.parsed["sentiment_label"] == "BEARISH"


def test_gemini_schema_drops_the_keywords_the_api_rejects() -> None:
    """Gemini 400s on maxItems, and SentimentValue.drivers carries one."""
    raw = ArticleSentimentBatch.model_json_schema()
    assert _has_key(raw, "maxItems")

    sanitized = _supported_schema(raw)

    assert not _has_key(sanitized, "maxItems")
    assert _has_key(sanitized, "pattern")
    assert not _has_key(_supported_schema(SentimentValue.model_json_schema()), "maxItems")


def _has_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False
