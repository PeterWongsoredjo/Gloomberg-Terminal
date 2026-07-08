"""The degradation ladder's live tiers: try the primary, cross-substitute on failure.

This owns Tier 0 (primary) and Tier 1 (cross-substitute) from 04 3.3. Each attempt is paced
under the RPM ceiling and guarded by the provider's breaker; a rate-limit or outage moves to
the next provider in the ladder. When every live provider is down it raises AllProvidersDown,
and the calling node takes Tier 2 (cache) then Tier 3 (degrade-visible).
"""

from __future__ import annotations

from app.agentic.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponse,
    ProviderSlot,
    ProviderUnavailable,
)


class AllProvidersDown(ProviderUnavailable):
    """Every live provider in the ladder was rate-limited, erroring, or breaker-open."""


class ProviderLadder:
    """Walks ordered provider slots, substituting past rate limits and outages."""

    def __init__(self, slots: list[ProviderSlot]) -> None:
        self._slots = slots

    @property
    def is_empty(self) -> bool:
        """True when no provider is wired into this ladder."""
        return not self._slots

    @property
    def primary_name(self) -> str:
        """The name of the first provider in the ladder, or 'none' when empty."""
        return self._slots[0].provider.name if self._slots else "none"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Returns the first live provider's response, substituting past transient faults."""
        attempted = False
        for slot in self._slots:
            if not slot.breaker.allow():
                continue
            attempted = True
            await slot.pacer.acquire()
            try:
                response = await slot.provider.complete(request)
            except (ProviderRateLimited, ProviderUnavailable):
                slot.breaker.record_failure()
                continue
            except ProviderError:
                slot.breaker.record_failure()
                raise
            slot.breaker.record_success()
            return response
        raise AllProvidersDown("no live provider available" if attempted else "all breakers open")
