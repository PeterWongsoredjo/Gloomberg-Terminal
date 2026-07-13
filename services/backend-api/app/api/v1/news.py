"""The news feed: paginated RSS headlines for the news column (additive)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import build_envelope
from app.serving.models import NewsFeedPage
from app.serving.pagination import CursorError, clamp_limit, decode_str_cursor, encode_str_cursor
from app.serving.readers.gold import ServingGoldReader

router = APIRouter()


@router.get("/news", response_model=Envelope[NewsFeedPage])
async def get_news_feed(
    app_state: AppState = Depends(get_app_state),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None),
) -> Envelope[NewsFeedPage]:
    """Newest headlines first, cursor-paginated, each item links to securities via tickers[]."""
    try:
        before = decode_str_cursor(cursor)
    except CursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    size = clamp_limit(limit)
    gold = ServingGoldReader(app_state.duckdb_ro)
    rows = await gold.news(size + 1, before)

    has_more = len(rows) > size
    page_rows = rows[:size]
    next_cursor = encode_str_cursor(str(page_rows[-1]["_cursor"])) if has_more else None
    page = NewsFeedPage(rows=[mappers.news_item(r) for r in page_rows], next_cursor=next_cursor)

    data_as_of = page_rows[0]["published_at"] if page_rows else datetime.now(UTC)
    trade_date = page_rows[0]["trade_date"] if page_rows else await anchor_trade_date(app_state)
    # news arrives continuously, not on the EOD clock, so no freshness SLO applies
    return build_envelope(page, data_as_of=data_as_of, trade_date=trade_date, slo_engine=app_state.slo_engine)
