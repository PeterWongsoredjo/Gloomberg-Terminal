from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class ProviderError(Exception):
    """A failure scoped to one provider, so the ladder tries the next one."""


class ProviderRateLimited(ProviderError):
    """The provider returned 429, so the ladder cross-substitutes and the breaker counts it."""


class ProviderUnavailable(ProviderError):
    """A 5xx, timeout, or connection error; retryable and breaker-counted."""


class ProviderRejected(ProviderError):
    """A 4xx that is permanent for this provider, like a dead model or a bad key."""


@dataclass
class ProviderRequest:
    """A single normalized inference request"""

    objective: str
    prompt_version: str
    system: str
    user: str
    response_model: type[BaseModel]
    temperature: float = 0.0
    seed: int = 42
    max_output_tokens: int = 512
    idempotency_key: str = ""
    estimated_tokens: int = 0


@dataclass
class ProviderResponse:
    """A single normalized inference response."""

    provider: str
    model: str
    raw_text: str
    parsed: dict[str, Any] | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    http_status: int
    error: str | None = None


@runtime_checkable
class Provider(Protocol):
    """The contract every adapter honors so nodes stay provider-agnostic."""

    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


@runtime_checkable
class QuotaGuard(Protocol):
    """The quota view the ladder consults to skip a provider before its hard 429."""

    def exhausted(self, provider: str) -> bool: ...

    def record(self, provider: str, *, requests: int, tokens: int) -> None: ...

    def consumption(self, provider: str) -> tuple[int, int]: ...


@dataclass
class ProviderSlot:
    """A provider plus the breaker and pacer that guard its rate budget."""

    provider: Provider
    breaker: Any
    pacer: Any
