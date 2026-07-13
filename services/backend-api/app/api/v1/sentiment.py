"""The Sentiment Matrix: per-security sentiment aggregated to a sector x label grid."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import build_envelope
from app.serving.models import SentimentMatrix
from app.serving.readers.postgres import ServingPostgresReader

router = APIRouter()


@router.get("/sentiment/matrix", response_model=Envelope[SentimentMatrix])
async def get_sentiment_matrix(app_state: AppState = Depends(get_app_state)) -> Envelope[SentimentMatrix]:
    """The sector x label sentiment grid, each cell carrying confidence and counts."""
    pg = ServingPostgresReader(app_state.pg_pool)
    rows = await pg.sentiment_rows()
    matrix = mappers.sentiment_matrix(rows)

    data_as_of = max((r["data_as_of"] for r in rows if r.get("data_as_of")), default=None) or datetime.now(UTC)
    trade_date = max((r["trade_date"] for r in rows if r.get("trade_date")), default=None) or await anchor_trade_date(app_state)
    # sentiment is a post-run artifact, not EOD-clocked, so it carries no freshness SLO
    return build_envelope(matrix, data_as_of=data_as_of, trade_date=trade_date, slo_engine=app_state.slo_engine)
