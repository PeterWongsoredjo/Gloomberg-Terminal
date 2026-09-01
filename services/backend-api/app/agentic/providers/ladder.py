"""
Ladder serves the connection, splits our gemini and Groq to tier 0 and 1
Primary will be tier 0, means the agent will be used for that task first,
if fails then switch to the tier 1 agent
"""

from __future__ import annotations

from app.agentic.providers.base import (
    ProviderError,
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
        """Tries each provider in turn, giving up only when every one is spent."""
        reasons: list[str] = []
        for slot in self._slots:
            name = slot.provider.name
            if not slot.breaker.allow():
                reasons.append(f"{name}: breaker open")
                continue
            if self._quota is not None and self._quota.exhausted(name):
                reasons.append(f"{name}: quota exhausted")
                continue
            await slot.pacer.acquire(request.estimated_tokens)
            try:
                response = await slot.provider.complete(request)
            except ProviderError as exc:
                # one provider being broken is never a reason to skip the others
                slot.breaker.record_failure()
                reasons.append(f"{name}: {exc}")
                continue
            slot.breaker.record_success()
            if self._quota is not None:
                self._quota.record(name, requests=1, tokens=response.prompt_tokens + response.completion_tokens)
            return response
        raise AllProvidersDown("; ".join(reasons) if reasons else "no providers configured")
