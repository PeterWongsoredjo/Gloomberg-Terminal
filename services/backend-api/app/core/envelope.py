from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.enums import QualityFlag, SessionPhase

DataT = TypeVar("DataT")


class Envelope(BaseModel, Generic[DataT]):
    """CT-011 — the only shape a serving payload may take (01 §4.10)."""

    api_version: str = "v1"
    served_at: datetime
    data_as_of: datetime
    freshness_slo_met: bool
    market_state: SessionPhase
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    data: DataT
