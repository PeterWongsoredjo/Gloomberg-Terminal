"""OB-04 cost & quota: notional pricing, quota tracking, and the ladder's pre-429 shift."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.agentic.providers.ladder import AllProvidersDown, ProviderLadder
from app.observability.cost import QuotaTracker, get_cost_model
from tests.agentic.conftest import ScriptedProvider, make_slot, sentiment_response


def test_notional_cost_uses_seeded_list_price() -> None:
    model = get_cost_model()
    cost = model.notional_cost("groq", 1000, 1000)
    assert cost == pytest.approx(0.00059 + 0.00079)


def test_quota_pct_is_the_tighter_of_requests_and_tokens() -> None:
    model = get_cost_model()
    # groq ceilings: rpd 1000, tpd 500000 -> 900/1000=0.9 beats 100000/500000=0.2
    assert model.quota_pct("groq", 900, 100000) == pytest.approx(0.9)


def test_tracker_rolls_over_on_a_new_wib_day() -> None:
    day = [date(2026, 7, 3)]
    tracker = QuotaTracker(get_cost_model(), threshold=0.95, today=lambda: day[0])
    tracker.record("groq", requests=500, tokens=100)
    assert tracker.consumption("groq") == (500, 100)
    day[0] = date(2026, 7, 4)
    assert tracker.consumption("groq") == (0, 0)


def test_tracker_flags_exhaustion_at_threshold() -> None:
    tracker = QuotaTracker(get_cost_model(), threshold=0.95, today=lambda: date(2026, 7, 3))
    tracker.record("groq", requests=960, tokens=0)  # 960/1000 = 0.96 >= 0.95
    assert tracker.exhausted("groq") is True
    assert tracker.exhausted("gemini") is False


def test_ladder_skips_an_exhausted_provider_before_the_hard_429() -> None:
    tracker = QuotaTracker(get_cost_model(), threshold=0.95, today=lambda: date(2026, 7, 3))
    tracker.record("groq", requests=1000, tokens=0)  # groq fully spent
    groq = make_slot(ScriptedProvider("groq", lambda r: sentiment_response(provider="groq")))
    gemini = make_slot(ScriptedProvider("gemini", lambda r: sentiment_response(provider="gemini")))
    ladder = ProviderLadder([groq, gemini], tracker)

    response = asyncio.run(ladder.complete(_request()))
    assert response.provider == "gemini"  # groq skipped on quota, substituted to gemini


def test_ladder_raises_when_all_providers_are_quota_exhausted() -> None:
    tracker = QuotaTracker(get_cost_model(), threshold=0.95, today=lambda: date(2026, 7, 3))
    tracker.record("groq", requests=1000, tokens=0)
    tracker.record("gemini", requests=1000, tokens=0)
    groq = make_slot(ScriptedProvider("groq", lambda r: sentiment_response(provider="groq")))
    gemini = make_slot(ScriptedProvider("gemini", lambda r: sentiment_response(provider="gemini")))
    ladder = ProviderLadder([groq, gemini], tracker)
    with pytest.raises(AllProvidersDown):
        asyncio.run(ladder.complete(_request()))


def _request() -> object:
    from app.agentic.providers.base import ProviderRequest
    from app.agentic.schemas import SentimentValue

    return ProviderRequest(
        objective="daily_sentiment",
        prompt_version="sent-v4",
        system="s",
        user="u",
        response_model=SentimentValue,
    )
