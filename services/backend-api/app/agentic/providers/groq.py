"""Groq adapter (Llama 3.3 70B): native JSON Object Mode, prompt-mandated AG-02 layout.

Groq guarantees syntactically valid JSON but not our keys or types, so the prompt spells out
the exact layout and the post-parse AG-02 validator is the arbiter. The Groq client is built
once in the FastAPI lifespan and injected here, never per node or per run.
"""

from __future__ import annotations

import time

import groq

from app.agentic.providers.base import (
    ProviderError,
    ProviderRateLimited,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailable,
    parse_json_object,
)


class GroqProvider:
    """Wraps an injected AsyncGroq client behind the normalized AG-05 interface."""

    name = "groq"

    def __init__(self, client: groq.AsyncGroq, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Runs one JSON-mode completion and normalizes the result or a typed fault."""
        started = time.monotonic()
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                response_format={"type": "json_object"},
                temperature=request.temperature,
                seed=request.seed,
                max_tokens=request.max_output_tokens,
            )
        except groq.RateLimitError as exc:
            raise ProviderRateLimited(f"groq 429: {exc}") from exc
        except (groq.APITimeoutError, groq.APIConnectionError) as exc:
            raise ProviderUnavailable(f"groq transport: {exc}") from exc
        except groq.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailable(f"groq {exc.status_code}") from exc
            raise ProviderError(f"groq {exc.status_code}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = completion.choices[0].message.content or ""
        usage = completion.usage
        return ProviderResponse(
            provider=self.name,
            model=self._model,
            raw_text=text,
            parsed=parse_json_object(text),
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            http_status=200,
        )
