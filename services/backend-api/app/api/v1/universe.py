"""The curated ticker universe, so a client can narrow a view to it."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.enums import QualityFlag
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving.envelope import build_envelope
from app.serving.models import CuratedUniverse
from app.serving.universe import get_universe

router = APIRouter()


@router.get("/universe", response_model=Envelope[CuratedUniverse])
async def get_curated_universe(
    app_state: AppState = Depends(get_app_state),
) -> Envelope[CuratedUniverse]:
    """The hand-picked tickers the insight flows cover, as-of when the file last changed."""
    curated = get_universe()
    # an empty list means the file failed to read, never that nothing is curated
    flags = [] if curated.tickers else [QualityFlag.MISSING_UPSTREAM]
    return build_envelope(
        CuratedUniverse(tickers=list(curated.tickers)),
        data_as_of=curated.as_of,
        trade_date=await anchor_trade_date(app_state),
        quality_flags=flags,
        slo_engine=app_state.slo_engine,
    )
