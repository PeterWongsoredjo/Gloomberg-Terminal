"""Merges the Gold news facts with the intraday poll projection for /news."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.serving.pagination import CursorError

NewsCursor = tuple[datetime, str]


def _as_utc(value: Any) -> datetime:
    """Any timestamp shape to an aware UTC datetime, naive means UTC."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decode_news_cursor(raw: str | None) -> NewsCursor | None:
    """Parses a published_at|item_id cursor, old and new timestamp formats alike."""
    if raw is None:
        return None
    timestamp, sep, item_id = raw.partition("|")
    if not sep or not item_id:
        raise CursorError("malformed news cursor")
    try:
        return _as_utc(timestamp), item_id
    except ValueError as exc:
        raise CursorError("malformed news cursor") from exc


def news_cursor(row: dict[str, Any]) -> str:
    """The canonical cursor string for a merged news row."""
    return f"{_as_utc(row['published_at']).isoformat()}|{row['item_id']}"


def _sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
    return _as_utc(row["published_at"]), str(row["item_id"])


def merge_news(
    gold_rows: list[dict[str, Any]], pg_rows: list[dict[str, Any]], size: int
) -> tuple[list[dict[str, Any]], bool]:
    """Newest-first union of both sources, deduped by item_id, Gold wins ties."""
    merged: dict[str, dict[str, Any]] = {}
    for row in [*gold_rows, *pg_rows]:
        merged.setdefault(str(row["item_id"]), row)
    ordered = sorted(merged.values(), key=_sort_key, reverse=True)
    return ordered[:size], len(ordered) > size


def freshest(gold: dict[str, Any] | None, pg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Of two optional rows, the one with the newest generated_at, Gold wins ties."""
    if gold is None:
        return pg
    if pg is None:
        return gold
    return pg if _as_utc(pg["generated_at"]) > _as_utc(gold["generated_at"]) else gold


def merge_latest_sentiment(
    gold: dict[str, dict[str, Any]], pg: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per ticker, the row with the newest generated_at wins."""
    merged = dict(gold)
    for ticker, row in pg.items():
        current = merged.get(ticker)
        if current is None or _as_utc(row["generated_at"]) > _as_utc(current["generated_at"]):
            merged[ticker] = row
    return merged
