"""Data Telemetry: a projection of the daily rollup for the telemetry panel."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.deps import get_app_state
from app.core.enums import QualityFlag
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.observability.rollup import read_latest_rollup, read_rollup
from app.serving import mappers
from app.serving.envelope import build_envelope
from app.serving.models import DataTelemetry

router = APIRouter()

_VALID_FLAGS = {f.value for f in QualityFlag}


@router.get("/telemetry", response_model=Envelope[DataTelemetry])
async def get_data_telemetry(
    app_state: AppState = Depends(get_app_state),
    trade_date: date | None = Query(default=None),
) -> Envelope[DataTelemetry]:
    """The daily telemetry rollup for a trade_date, defaulting to the latest published date."""
    if app_state.pg_pool is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="telemetry store unavailable")

    row = (
        await read_rollup(app_state.pg_pool, trade_date)
        if trade_date is not None
        else await read_latest_rollup(app_state.pg_pool)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no telemetry for trade_date")

    record = dict(row)
    resolved = record["trade_date"]
    telemetry = mappers.data_telemetry(record)
    # the rollup self-evaluates its SLOs and the panel renders them, so the envelope stays fresh
    return build_envelope(
        telemetry,
        data_as_of=record["data_as_of"],
        trade_date=resolved,
        quality_flags=_flags(record.get("quality_flags")),
        slo_engine=app_state.slo_engine,
    )


def _flags(raw: object) -> list[QualityFlag]:
    import json

    values = raw if isinstance(raw, list) else (json.loads(raw) if isinstance(raw, str) and raw else [])
    return [QualityFlag(f) for f in values if f in _VALID_FLAGS]
