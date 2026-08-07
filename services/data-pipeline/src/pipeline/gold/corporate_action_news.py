"""
Reads corporate actions shaped as feed items out of the published Gold snapshot.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import duckdb

from pipeline.config import Settings, get_settings

LOOKBACK_DAYS = 90
LOOKAHEAD_DAYS = 7

_SELECT = """
select item_id, source, lang, title, summary, url, published_at, tickers, item_type
from fct_corporate_action_news
where trade_date between ? and ?
order by published_at desc, item_id desc
"""


def window_for(trade_date: date) -> tuple[date, date]:
    """The effective-date range a run projects, matching the Gold model's own window."""
    return trade_date - timedelta(days=LOOKBACK_DAYS), trade_date + timedelta(days=LOOKAHEAD_DAYS)


def _as_item(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "item_id": row[0],
        "source": row[1],
        "lang": row[2],
        "title": row[3],
        "summary": row[4],
        "url": row[5],
        "published_at": row[6],
        "tickers": list(row[7] or []),
        "candidate_tickers": [],
        "item_type": row[8],
    }


def read_items(trade_date: date, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Corporate-action feed items in the run's window, empty when Gold has none yet."""
    resolved = settings or get_settings()
    gold = resolved.published_gold
    if not gold.exists():
        return []
    since, until = window_for(trade_date)
    con = duckdb.connect(str(gold), read_only=True)
    try:
        rows = con.execute(_SELECT, [since, until]).fetchall()
    except duckdb.CatalogException:
        return []
    finally:
        con.close()
    return [_as_item(r) for r in rows]


def main() -> None:
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    items = read_items(today)
    print(f"{len(items)} corporate-action feed items in window for {today}")
    for item in items:
        print(f"  {item['item_id']}  {item['title']}")


if __name__ == "__main__":
    main()
