"""The securities universe: a paginated list and a per-ticker snapshot."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import DATASET_DAILY_TRADE, build_envelope
from app.serving.models import PriceSeries, SecurityPage, SecuritySnapshot
from app.serving.pagination import Cursor, CursorError, clamp_limit, decode_cursor, encode_cursor
from app.serving.readers.gold import ServingGoldReader
from app.serving.readers.postgres import ServingPostgresReader

router = APIRouter()

# chart range -> trading days of daily bars to return
_RANGE_DAYS = {"1M": 22, "3M": 66, "6M": 132, "1Y": 252, "ALL": 100_000}
_DEFAULT_RANGE = "6M"


@router.get("/securities", response_model=Envelope[SecurityPage])
async def list_securities(
    app_state: AppState = Depends(get_app_state),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None),
) -> Envelope[SecurityPage]:
    """One keyset page of the universe, ordered by ticker, with an opaque next-page cursor."""
    try:
        parsed = decode_cursor(cursor)
    except CursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    size = clamp_limit(limit)
    pg = ServingPostgresReader(app_state.pg_pool)
    after_ticker = str(parsed.sort_value) if parsed else None
    after_id = parsed.security_id if parsed else None
    rows = await pg.securities_page(after_ticker, after_id, size)

    has_more = len(rows) > size
    page_rows = rows[:size]
    next_cursor = (
        encode_cursor(Cursor(sort_value=page_rows[-1]["ticker"], security_id=page_rows[-1]["security_id"]))
        if has_more
        else None
    )
    page = SecurityPage(rows=[mappers.security_row(r) for r in page_rows], next_cursor=next_cursor)

    trade_date = await anchor_trade_date(app_state)
    return build_envelope(page, data_as_of=datetime.now(UTC), trade_date=trade_date, slo_engine=app_state.slo_engine)


@router.get("/securities/{ticker}", response_model=Envelope[SecuritySnapshot])
async def get_security(
    ticker: str, app_state: AppState = Depends(get_app_state)
) -> Envelope[SecuritySnapshot]:
    """Identity, latest close, integrity, and badges for one security."""
    pg = ServingPostgresReader(app_state.pg_pool)
    row = await pg.security_snapshot(ticker.upper())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="security not found")

    snapshot = mappers.security_snapshot(row)
    return build_envelope(
        snapshot,
        data_as_of=row["data_as_of"],
        trade_date=row["trade_date"],
        dataset=DATASET_DAILY_TRADE,
        quality_flags=snapshot.dq_flags,
        slo_engine=app_state.slo_engine,
    )


@router.get("/securities/{ticker}/candles", response_model=Envelope[PriceSeries])
async def get_candles(
    ticker: str,
    app_state: AppState = Depends(get_app_state),
    range: str = Query(default=_DEFAULT_RANGE),
) -> Envelope[PriceSeries]:
    limit_days = _RANGE_DAYS.get(range.upper())
    if limit_days is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown range: {range}")

    gold = ServingGoldReader(app_state.duckdb_ro)
    rows = await gold.candles(ticker.upper(), limit_days)
    series = PriceSeries(ticker=ticker.upper(), bars=[mappers.candle(r) for r in rows])

    last = rows[-1]["trade_date"] if rows else await anchor_trade_date(app_state)
    data_as_of = datetime.combine(last, datetime.min.time(), UTC) if rows else datetime.now(UTC)
    flags = series.bars[-1].dq_flags if series.bars else []
    return build_envelope(
        series,
        data_as_of=data_as_of,
        trade_date=last,
        dataset=DATASET_DAILY_TRADE,
        quality_flags=flags,
        slo_engine=app_state.slo_engine,
    )
