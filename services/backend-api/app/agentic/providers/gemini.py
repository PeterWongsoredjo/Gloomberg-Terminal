"""
Gemini adapter (2.5 Flash-Lite)
"""

from __future__ import annotations

import time
from typing import Any

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


_UNSUPPORTED_SCHEMA_KEYS = frozenset({"maxItems", "minItems"})


def _supported_schema(node: Any) -> Any:
    """Drops keywords Gemini rejects; pydantic still enforces them on the way back."""
    if isinstance(node, dict):
        return {k: _supported_schema(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(node, list):
        return [_supported_schema(v) for v in node]
    return node


class GeminiProvider:
    name = "gemini"

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.monotonic()
        # raw JSON Schema lane: the typed lane rejects our strict additionalProperties
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_json_schema=_supported_schema(request.response_model.model_json_schema()),
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
            raise ProviderError(f"gemini {exc.code}: {str(exc)[:160]}") from exc
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
