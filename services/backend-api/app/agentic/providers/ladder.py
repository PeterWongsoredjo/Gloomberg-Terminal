"""
Ladder serves the connection, splits our gemini and Groq to tier 0 and 1
Primary will be tier 0, means the agent will be used for that task first,
if fails then switch to the tier 1 agent
"""

from __future__ import annotations

from app.agentic.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponse,
    ProviderSlot,
    ProviderUnavailable,
    QuotaGuard,
)


class AllProvidersDown(ProviderUnavailable):
    """Every live provider in the ladder was rate-limited, erroring, or breaker-open."""


class ProviderLadder:
    def __init__(self, slots: list[ProviderSlot], quota: QuotaGuard | None = None) -> None:
        self._slots = slots
        self._quota = quota

    @property
    def is_empty(self) -> bool:
        return not self._slots

    @property
    def primary_name(self) -> str:
        return self._slots[0].provider.name if self._slots else "none"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        attempted = False
        for slot in self._slots:
            name = slot.provider.name
            if not slot.breaker.allow():
                continue
            if self._quota is not None and self._quota.exhausted(name):
                continue
            attempted = True
            await slot.pacer.acquire(request.estimated_tokens)
            try:
                response = await slot.provider.complete(request)
            except (ProviderRateLimited, ProviderUnavailable):
                slot.breaker.record_failure()
                continue
            except ProviderError:
                slot.breaker.record_failure()
                raise
            slot.breaker.record_success()
            if self._quota is not None:
                self._quota.record(name, requests=1, tokens=response.prompt_tokens + response.completion_tokens)
            return response
        raise AllProvidersDown("no live provider available" if attempted else "all breakers open or quota-exhausted")
