"""
Tracing out a node will use Langfuse to observe how each node acts
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, Protocol


class Span(Protocol):
    """One node's unit of work, capturing output and token usage for a trace."""

    def set_output(self, output: Any) -> None: ...

    def set_usage(self, prompt_tokens: int, completion_tokens: int) -> None: ...

    def set_error(self, message: str) -> None: ...


class Tracer(Protocol):
    def span(self, name: str, run_id: str, inputs: dict[str, Any] | None = None) -> Any: ...


class _NoopSpan:
    def set_output(self, output: Any) -> None:
        return None

    def set_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        return None

    def set_error(self, message: str) -> None:
        return None


class NoopTracer:
    @asynccontextmanager
    async def span(
        self, name: str, run_id: str, inputs: dict[str, Any] | None = None
    ) -> AsyncIterator[_NoopSpan]:
        """Yields a no-op span so node instrumentation is always safe to call."""
        yield _NoopSpan()
