"""
defines correlation keys and the telemetry event schema
enforces lineage rules requiring trade dates and drill down anchors on non meta events
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agentic.ids import new_ulid

Plane = Literal["LLM", "EVAL", "PIPELINE", "META"]
Severity = Literal["INFO", "WARN", "ERROR", "CRITICAL"]
Kind = Literal[
    "span_end", "cost", "slo_eval", "alert", "dbt_test", "coverage", "heartbeat", "run_end"
]

# severity rank so the spool knows what it may drop first (§3.9)
SEVERITY_RANK: dict[str, int] = {"INFO": 0, "WARN": 1, "ERROR": 2, "CRITICAL": 3}

_DRILLDOWN_ANCHORS = ("run_id", "trace_id", "ingest_run_id", "dbt_invocation_id")


class Correlation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    trade_date: date | None = None
    trace_id: str | None = None
    ingest_run_id: str | None = None
    dbt_invocation_id: str | None = None
    prompt_version: str | None = None
    security_id: int | None = None


class Measure(BaseModel):
    """numeric or string data value measured along with its name and unit"""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | int | bool | str | None
    unit: str


class TelemetryEvent(BaseModel):
    """event envelope schema containing metadata and payload measures"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    event_id: str = Field(default_factory=new_ulid)
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plane: Plane
    kind: Kind
    correlation: Correlation
    measure: Measure
    attributes: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "INFO"

    @model_validator(mode="after")
    def _require_anchor(self) -> TelemetryEvent:
        """verifies correlation details are set correctly"""
        if self.correlation.trade_date is None:
            raise ValueError("telemetry event has no trade_date anchor (OB-01)")
        if self.plane == "META":
            return self
        if not any(getattr(self.correlation, a) for a in _DRILLDOWN_ANCHORS):
            raise ValueError("non-META telemetry event needs at least one drill-down anchor (OB-01)")
        return self

