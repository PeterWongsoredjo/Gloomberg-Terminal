"""Gemini adapter (2.5 Flash-Lite): true structured outputs via response_schema.

Passing the AG-02 Pydantic class as response_schema constrains generation so keys and types
conform by construction, the strongest guarantee we have; the same AG-02 validator still runs
after, so a ladder cross-substitution from Groq carries no schema risk. The client is built
once in the lifespan and injected.
"""

from __future__ import annotations

import time

from google import genai
from google.genai import errors, types

from app.agentic.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailable,
    parse_json_object,
)


class GeminiProvider:
    """Wraps an injected google-genai client behind the normalized AG-05 interface."""

    name = "gemini"

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Runs one schema-constrained completion and normalizes the result or a typed fault."""
        started = time.monotonic()
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_schema=request.response_model,
            temperature=request.temperature,
            seed=request.seed,
            max_output_tokens=request.max_output_tokens,
        )
        try:
            result = await self._client.aio.models.generate_content(
                model=self._model, contents=request.user, config=config
            )
        except errors.ClientError as exc:
            if exc.code == 429:
                raise ProviderRateLimited(f"gemini 429: {exc}") from exc
            raise ProviderError(f"gemini {exc.code}") from exc
        except errors.ServerError as exc:
            raise ProviderUnavailable(f"gemini {exc.code}") from exc
        except errors.APIError as exc:
            raise ProviderUnavailable(f"gemini transport: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = result.text or ""
        usage = result.usage_metadata
        return ProviderResponse(
            provider=self.name,
            model=self._model,
            raw_text=text,
            parsed=parse_json_object(text),
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            latency_ms=latency_ms,
            http_status=200,
        )
