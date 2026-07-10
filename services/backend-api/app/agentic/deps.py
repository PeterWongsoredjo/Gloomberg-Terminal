"""What gets passed around to every node in our agentic graph
This packs:
- Active LLM provider connections (slots anyway)
- DB Pools (Postgres / DuckDB)
- the Settings (limits, etc.)
- Tracing / Quota guards
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import asyncpg
import duckdb

from app.agentic.config import AgenticSettings
from app.agentic.objectives import spec_for
from app.agentic.providers.base import ProviderSlot, QuotaGuard
from app.agentic.providers.ladder import ProviderLadder
from app.agentic.tracing import Tracer


@dataclass
class GraphDeps:
    slots: dict[str, ProviderSlot]
    pg_pool: asyncpg.Pool | None
    duckdb_ro: duckdb.DuckDBPyConnection | None
    settings: AgenticSettings
    tracer: Tracer
    quota: QuotaGuard | None = None

    def ladder(self, names: Iterable[str]) -> ProviderLadder:
        """Builds a sort of gateway, Gemini or Groq, if one fails, then the backup is the other"""
        return ProviderLadder([self.slots[n] for n in names if n in self.slots], self.quota)

    def ladder_for(self, objective: str) -> ProviderLadder:
        """this is to map to ladder easily, based on the task in objectives.py"""
        return self.ladder(spec_for(objective).ladder)
