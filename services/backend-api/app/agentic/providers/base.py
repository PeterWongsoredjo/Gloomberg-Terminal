"""AG-05 normalized provider interface: one request/response shape for Groq and Gemini.

Nodes talk to this interface, never to a vendor SDK, so the degradation ladder can swap
providers without touching node logic. Transport faults raise typed errors the ladder reads;
a 200 with unparseable JSON is not a transport fault, it returns with parsed=None for the
validator to reject.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parses model text to a JSON object, or None so the AG-02 validator rejects it."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class ProviderError(Exception):
    """A permanent provider failure; do not retry or cross-substitute on this."""


class ProviderRateLimited(ProviderError):
    """The provider returned 429; the ladder cross-substitutes and the breaker counts it."""


class ProviderUnavailable(ProviderError):
    """A 5xx, timeout, or connection error; retryable and breaker-counted."""


@dataclass
class ProviderRequest:
    """A single normalized inference request (AG-05)."""

    objective: str
    prompt_version: str
    system: str
    user: str
    response_model: type[BaseModel]
    temperature: float = 0.0
    seed: int = 42
    max_output_tokens: int = 512
    idempotency_key: str = ""


@dataclass
class ProviderResponse:
    """A single normalized inference response (AG-05)."""

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


@dataclass
class ProviderSlot:
    """A provider plus the breaker and pacer that guard its rate budget."""

    provider: Provider
    breaker: Any
    pacer: Any
