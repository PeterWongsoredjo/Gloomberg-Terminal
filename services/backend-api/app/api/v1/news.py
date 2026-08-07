"""The news feed: paginated RSS headlines for the news column (additive)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.deps import anchor_trade_date, get_app_state
from app.core.envelope import Envelope
from app.lifespan import AppState
from app.serving import mappers
from app.serving.envelope import build_envelope
from app.serving.models import NewsFeedPage
from app.serving.news_merge import (
    decode_news_cursor,
    merge_article_sentiment,
    merge_news,
    merge_ticker_breakdown,
    news_cursor,
)
from app.serving.pagination import CursorError, clamp_limit, decode_str_cursor, encode_str_cursor
from app.serving.readers.gold import ServingGoldReader
from app.serving.readers.postgres import ServingPostgresReader

router = APIRouter()


@router.get("/news", response_model=Envelope[NewsFeedPage])
async def get_news_feed(
    app_state: AppState = Depends(get_app_state),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None),
) -> Envelope[NewsFeedPage]:
    """Newest headlines first, cursor-paginated, each item links to securities via tickers[]."""
    try:
        before = decode_news_cursor(decode_str_cursor(cursor))
    except CursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    size = clamp_limit(limit)
    gold = ServingGoldReader(app_state.duckdb_ro)
    # the intraday reads degrade to empty when the pool is down, leaving Gold-only
    pg = ServingPostgresReader(app_state.pg_pool)
    gold_rows = await gold.news(size + 1, before)
    corp_rows = await gold.corporate_action_news(size + 1, before)
    pg_rows = await pg.intraday_news(size + 1, before)
    page_rows, has_more = merge_news([*gold_rows, *corp_rows], pg_rows, size)

    next_cursor = encode_str_cursor(news_cursor(page_rows[-1])) if has_more else None
    item_ids = [str(r["item_id"]) for r in page_rows]
    sentiment = merge_article_sentiment(
        await gold.article_sentiment(item_ids), await pg.article_sentiment(item_ids)
    )
    breakdown = merge_ticker_breakdown(
        await gold.article_ticker_sentiment(item_ids), await pg.article_ticker_sentiment(item_ids)
    )
    # the ledger hop resolves run/trace ids; a down pool degrades to no run link, never a 500
    provenance = await pg.artifact_provenance(_artifact_ids(sentiment))
    page = NewsFeedPage(
        rows=[mappers.news_item(r, sentiment, breakdown, provenance) for r in page_rows],
        next_cursor=next_cursor,
    )

    data_as_of = page_rows[0]["published_at"] if page_rows else datetime.now(UTC)
    trade_date = page_rows[0]["trade_date"] if page_rows else await anchor_trade_date(app_state)
    # news arrives continuously, not on the EOD clock, so no freshness SLO applies
    return build_envelope(page, data_as_of=data_as_of, trade_date=trade_date, slo_engine=app_state.slo_engine)


def _artifact_ids(sentiment_by_item: dict[str, dict[str, Any]]) -> list[str]:
    """The distinct sentiment artifact ids on this page, for the batched ledger lookup."""
    seen: dict[str, None] = {}
    for sentiment in sentiment_by_item.values():
        artifact_id = sentiment.get("artifact_id")
        if artifact_id is not None:
            seen.setdefault(str(artifact_id), None)
    return list(seen)
