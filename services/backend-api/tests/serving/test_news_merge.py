"""Pure unit tests for the two-source /news merge helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.serving.news_merge import decode_news_cursor, merge_latest_sentiment, merge_news, news_cursor
from app.serving.pagination import CursorError


def _row(item_id: str, published_at: Any) -> dict[str, Any]:
    return {"item_id": item_id, "published_at": published_at, "title": f"headline {item_id}"}


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 14, hour, minute, tzinfo=timezone.utc)


def test_cursor_round_trips() -> None:
    cursor = news_cursor(_row("cnbc:aa", datetime(2026, 7, 14, 3, 0)))
    assert decode_news_cursor(cursor) == (_utc(3), "cnbc:aa")


def test_cursor_tolerates_legacy_duckdb_format() -> None:
    assert decode_news_cursor("2026-07-14 03:00:00|cnbc:aa") == (_utc(3), "cnbc:aa")


def test_cursor_none_passthrough() -> None:
    assert decode_news_cursor(None) is None


def test_cursor_malformed_raises() -> None:
    with pytest.raises(CursorError):
        decode_news_cursor("no separator here")
    with pytest.raises(CursorError):
        decode_news_cursor("not-a-date|item")


def test_merge_orders_newest_first_across_sources() -> None:
    gold = [_row("g1", _utc(5)), _row("g2", _utc(3))]
    pg = [_row("p1", _utc(4))]
    rows, has_more = merge_news(gold, pg, 10)
    assert [r["item_id"] for r in rows] == ["g1", "p1", "g2"]
    assert has_more is False


def test_merge_mixes_naive_and_aware_timestamps() -> None:
    gold = [_row("g1", datetime(2026, 7, 14, 5, 0))]
    pg = [_row("p1", _utc(6))]
    rows, _ = merge_news(gold, pg, 10)
    assert [r["item_id"] for r in rows] == ["p1", "g1"]


def test_merge_dedupes_by_item_id_gold_wins() -> None:
    gold = [{**_row("dup", _utc(5)), "source": "gold"}]
    pg = [{**_row("dup", _utc(5)), "source": "pg"}]
    rows, _ = merge_news(gold, pg, 10)
    assert len(rows) == 1 and rows[0]["source"] == "gold"


def test_merge_cuts_to_size_and_flags_more() -> None:
    gold = [_row(f"g{i}", _utc(10 - i)) for i in range(3)]
    pg = [_row("p1", _utc(9, 30))]
    rows, has_more = merge_news(gold, pg, 2)
    assert [r["item_id"] for r in rows] == ["g0", "p1"]
    assert has_more is True


def test_pagination_across_the_seam() -> None:
    """Page two picks up exactly after page one's last row, whichever source it was."""
    gold = [_row("g1", _utc(8)), _row("g2", _utc(6))]
    pg = [_row("p1", _utc(7)), _row("p2", _utc(5))]
    page_one, _ = merge_news(gold, pg, 2)
    cursor = decode_news_cursor(news_cursor(page_one[-1]))
    assert cursor is not None

    def after(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if (r["published_at"], r["item_id"]) < cursor]

    page_two, _ = merge_news(after(gold), after(pg), 2)
    assert [r["item_id"] for r in page_one] == ["g1", "p1"]
    assert [r["item_id"] for r in page_two] == ["g2", "p2"]


def _sent(generated_at: Any, source: str) -> dict[str, Any]:
    return {"ticker": "BBCA", "generated_at": generated_at, "source": source}


def test_sentiment_freshest_wins_intraday_newer() -> None:
    merged = merge_latest_sentiment(
        {"BBCA": _sent(_utc(2), "gold")}, {"BBCA": _sent(_utc(4), "pg")}
    )
    assert merged["BBCA"]["source"] == "pg"


def test_sentiment_freshest_wins_eod_newer() -> None:
    merged = merge_latest_sentiment(
        {"BBCA": _sent(_utc(10), "gold")}, {"BBCA": _sent(_utc(4), "pg")}
    )
    assert merged["BBCA"]["source"] == "gold"


def test_sentiment_naive_gold_timestamp_treated_as_utc() -> None:
    merged = merge_latest_sentiment(
        {"BBCA": _sent(datetime(2026, 7, 14, 10, 0), "gold")}, {"BBCA": _sent(_utc(4), "pg")}
    )
    assert merged["BBCA"]["source"] == "gold"


def test_sentiment_union_keeps_single_source_tickers() -> None:
    merged = merge_latest_sentiment(
        {"BBCA": _sent(_utc(2), "gold")}, {"TLKM": _sent(_utc(4), "pg")}
    )
    assert set(merged) == {"BBCA", "TLKM"}
