"""
Ladder serves the connection, splits our gemini and Groq to tier 0 and 1
Primary will be tier 0, means the agent will be used for that task first,
if fails then switch to the tier 1 agent
"""

from __future__ import annotations

import time

from app.agentic.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderRejected,
    ProviderRequest,
    ProviderResponse,
    ProviderSlot,
    ProviderUnavailable,
    QuotaGuard,
)
from app.observability.metrics import record_fallback, record_llm_attempt


class AllProvidersDown(ProviderUnavailable):
    """Every live provider in the ladder was rate-limited, erroring, or breaker-open."""


def failure_outcome(exc: Exception) -> str:
    """The low-cardinality outcome label for a failed provider call."""
    if isinstance(exc, ProviderRateLimited):
        return "rate_limited"
    if isinstance(exc, ProviderRejected):
        return "rejected"
    if isinstance(exc, ProviderUnavailable):
        return "unavailable"
    return "error"


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
        passed_over: tuple[str, str] | None = None
        for slot in self._slots:
            name = slot.provider.name
            if not slot.breaker.allow():
                reasons.append(f"{name}: breaker open")
                passed_over = (name, "breaker_open")
                continue
            if self._quota is not None and self._quota.exhausted(name):
                reasons.append(f"{name}: quota exhausted")
                passed_over = (name, "quota_exhausted")
                continue
            if passed_over is not None:
                record_fallback(request.objective, passed_over[0], name, passed_over[1])
            await slot.pacer.acquire(request.estimated_tokens)
            started = time.perf_counter()
            try:
                response = await slot.provider.complete(request)
            except ProviderError as exc:
                record_llm_attempt(name, request.objective, failure_outcome(exc), time.perf_counter() - started)
                # one provider being broken is never a reason to skip the others
                slot.breaker.record_failure()
                reasons.append(f"{name}: {exc}")
                passed_over = (name, "error")
                continue
            except Exception:
                record_llm_attempt(name, request.objective, "error", time.perf_counter() - started)
                raise
            record_llm_attempt(name, request.objective, "success", time.perf_counter() - started)
            slot.breaker.record_success()
            if self._quota is not None:
                self._quota.record(name, requests=1, tokens=response.prompt_tokens + response.completion_tokens)
            return response
        raise AllProvidersDown("; ".join(reasons) if reasons else "no providers configured")
