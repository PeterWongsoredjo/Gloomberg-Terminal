"""The tracing seam every node reports through, so a real backend can plug in later.

Each node opens a span; the default sink does nothing. Stage 4 swaps in a Langfuse-backed
tracer implementing the same interface, so wiring real observability is an injection, not a
rewrite of the nodes.
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
    """Opens spans under a run; the interface a Langfuse tracer will implement."""

    def span(self, name: str, run_id: str, inputs: dict[str, Any] | None = None) -> Any: ...


class _NoopSpan:
    """A span that records nothing; the default when no tracer is wired."""

    def set_output(self, output: Any) -> None:
        return None

    def set_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        return None

    def set_error(self, message: str) -> None:
        return None


class NoopTracer:
    """The default tracer: spans open and close but capture nothing."""

    @asynccontextmanager
    async def span(
        self, name: str, run_id: str, inputs: dict[str, Any] | None = None
    ) -> AsyncIterator[_NoopSpan]:
        """Yields a no-op span so node instrumentation is always safe to call."""
        yield _NoopSpan()
