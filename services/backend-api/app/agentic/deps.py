"""Process-scoped resources the graph nodes read, injected through the run config.

Everything here is built once in the FastAPI lifespan and passed by reference into the compiled
graph, so a node never constructs a client, pool, or connection itself. Nodes pull deps from
config["configurable"]["deps"].
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import asyncpg
import duckdb

from app.agentic.config import AgenticSettings
from app.agentic.objectives import spec_for
from app.agentic.providers.base import ProviderSlot
from app.agentic.providers.ladder import ProviderLadder
from app.agentic.tracing import Tracer


@dataclass
class GraphDeps:
    """The injected bundle every node reads its resources from."""

    slots: dict[str, ProviderSlot]
    pg_pool: asyncpg.Pool | None
    duckdb_ro: duckdb.DuckDBPyConnection | None
    settings: AgenticSettings
    tracer: Tracer

    def ladder(self, names: Iterable[str]) -> ProviderLadder:
        """Builds a ladder over the named live providers, skipping ones not wired."""
        return ProviderLadder([self.slots[n] for n in names if n in self.slots])

    def ladder_for(self, objective: str) -> ProviderLadder:
        """The primary-then-substitute ladder for an objective."""
        return self.ladder(spec_for(objective).ladder)
