"""Market session state: the phase for the shell plus the headline index."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends

from app.api.v1.deps import get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.observability.calendar import get_calendar
from app.serving.envelope import DATASET_DAILY_TRADE, build_envelope
from app.serving.models import IndexLevel, MarketState
from app.serving.readers.gold import ServingGoldReader

router = APIRouter()

_IHSG = "IHSG"


@router.get("/market/state", response_model=Envelope[MarketState])
async def get_market_state(app_state: AppState = Depends(get_app_state)) -> Envelope[MarketState]:
    """The current session phase, trading-day flag, close time, and IHSG level."""
    gold = ServingGoldReader(app_state.duckdb_ro)
    latest = await gold.latest_trade_date()
    trade_date = date.fromisoformat(latest) if latest else datetime.now(UTC).date()

    calendar = get_calendar()
    index_row = await gold.headline_index(_IHSG)
    ihsg = _index_level(index_row)
    data_as_of = (
        datetime.combine(index_row["trade_date"], datetime.min.time(), UTC) if index_row else datetime.now(UTC)
    )

    state = MarketState(
        trade_date=trade_date,
        session_phase=calendar.market_state(trade_date),
        is_trading_day=calendar.is_trading_day(trade_date),
        close_time_utc=calendar.close_datetime_utc(trade_date),
        ihsg=ihsg,
    )
    return build_envelope(
        state,
        data_as_of=data_as_of,
        trade_date=trade_date,
        dataset=DATASET_DAILY_TRADE,
        slo_engine=app_state.slo_engine,
    )

    
def _index_level(row: dict[str, object] | None) -> IndexLevel | None:
    if row is None:
        return None
    return IndexLevel(
        index_code=str(row["index_id"]),
        level=float(row["close_level"]),  # type: ignore[arg-type]
        change=None if row.get("change_level") is None else float(row["change_level"]),  # type: ignore[arg-type]
    )
