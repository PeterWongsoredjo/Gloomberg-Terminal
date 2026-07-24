"""The Live Tape: a REST snapshot and the streaming WebSocket."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, WebSocket

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import DATASET_DAILY_TRADE, build_envelope
from app.serving.models import LiveTapePage
from app.serving.readers.postgres import ServingPostgresReader
from app.serving.tape.manager import TapeStreamer

router = APIRouter()


@router.get("/tape", response_model=Envelope[LiveTapePage])
async def get_tape_snapshot(app_state: AppState = Depends(get_app_state)) -> Envelope[LiveTapePage]:
    """The full tape as a one-shot REST read, the WebSocket streams the same rows live."""
    pg = ServingPostgresReader(app_state.pg_pool)
    rows = await pg.live_tape()
    tape_rows = [mappers.tape_row(r) for r in rows]

    data_as_of = max((r["data_as_of"] for r in rows if r.get("data_as_of")), default=datetime.now(UTC))
    trade_date = max((r["trade_date"] for r in rows if r.get("trade_date")), default=None) or await anchor_trade_date(app_state)
    flags = sorted({f for row in tape_rows for f in row.dq_flags}, key=lambda f: f.value)

    page = LiveTapePage(trade_date=trade_date, rows=tape_rows)
    return build_envelope(
        page,
        data_as_of=data_as_of,
        trade_date=trade_date,
        dataset=DATASET_DAILY_TRADE,
        quality_flags=flags,
        slo_engine=app_state.slo_engine,
    )


@router.websocket("/tape/stream")
async def stream_tape(
    websocket: WebSocket,
    app_state: AppState = Depends(get_app_state),
) -> None:
    """Streams snapshot then deltas, a public read like the tape REST snapshot."""
    await websocket.accept()
    reader = ServingPostgresReader(app_state.pg_pool)
    await TapeStreamer(websocket, reader, app_state.slo_engine).run()
