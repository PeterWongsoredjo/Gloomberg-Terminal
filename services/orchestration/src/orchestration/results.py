from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PhaseResult:
    status: str  # SUCCESS | PARTIAL | FAILED | SKIPPED | DEGRADED
    payload: Any = None
    notes: str = ""
    ingest_run_id: str | None = None
    dbt_invocation_id: str | None = None
    run_id: str | None = None
    gate: dict[str, Any] | None = None
