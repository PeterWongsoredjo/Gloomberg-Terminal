"""
Provides read-only SQL access to the published Gold Data snapshot in DuckDB.

Every call runs on its own cursor inside a worker thread so a blocking DuckDB scan never stalls the
async runtime. A missing snapshot yields empty context, not a crash.
"""

from __future__ import annotations

import asyncio
from typing import Any

import duckdb


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


class GoldReader:
    def __init__(self, connection: duckdb.DuckDBPyConnection | None) -> None:
        self._connection = connection

    async def _rows(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        """Runs one query on a fresh cursor in a worker thread, returning dict rows."""
        if self._connection is None:
            return []

        def _run() -> list[dict[str, Any]]:
            cursor = self._connection.cursor()  # type: ignore[union-attr]
            try:
                cursor.execute(sql, params)
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            finally:
                cursor.close()

        return await asyncio.to_thread(_run)

    async def current_tickers(self) -> set[str]:
        rows = await self._rows("select ticker from dim_security where is_current", [])
        return {str(r["ticker"]) for r in rows}

    async def market_context(self, trade_date: str, universe: list[str]) -> list[dict[str, Any]]:
        if not universe:
            return []
        sql = f"""
            select
                s.security_id, s.ticker, s.board, s.is_fca, s.sector_idxic,
                s.special_notation, t.close_adj_idr, t.daily_return, t.net_foreign_idr,
                t.price_series_integrity, t.dq_flags
            from dim_security s
            left join fct_daily_trade t
                on s.security_id = t.security_id and t.trade_date = ?
            where s.is_current and s.ticker in ({_placeholders(universe)})
        """
        return await self._rows(sql, [trade_date, *universe])

    async def index_context(self, index_id: str, trade_date: str) -> dict[str, Any] | None:
        """The latest index level at or before the date, shaped like a market context row."""
        sql = """
            select index_id, trade_date, close_level, change_level
            from fct_index_level
            where index_id = ? and trade_date <= ?
            order by trade_date desc
            limit 1
        """
        try:
            rows = await self._rows(sql, [index_id, trade_date])
        except duckdb.CatalogException:
            return None  # index facts not yet published to this snapshot
        if not rows:
            return None
        row = rows[0]
        return {
            "security_id": None,
            "ticker": index_id,
            "board": "INDEX",
            "is_fca": False,
            "sector_idxic": None,
            "special_notation": [],
            "close_level": row["close_level"],
            "change_level": row["change_level"],
            "level_as_of": str(row["trade_date"]),
        }

    async def corporate_actions(self, universe: list[str], trade_date: str) -> list[dict[str, Any]]:
        if not universe:
            return []
        sql = f"""
            select event_id, security_id, ticker, event_type, effective_trade_date,
                   price_adjustment_required, adjustment_pending
            from fct_corporate_action
            where ticker in ({_placeholders(universe)})
              and effective_trade_date between (cast(? as date) - 30) and (cast(? as date) + 5)
        """
        return await self._rows(sql, [*universe, trade_date, trade_date])

    async def latest_trade_date(self) -> str | None:
        """The newest published trading day, for anchoring intraday context."""
        rows = await self._rows("select max(trade_date) as trade_date from fct_daily_trade", [])
        value = rows[0]["trade_date"] if rows else None
        return str(value) if value is not None else None

    async def news_items(self, trade_date: str) -> list[dict[str, Any]]:
        sql = """
            select item_id, trade_date, source, lang, title, summary, url,
                   published_at, tickers
            from fct_news_item
            where trade_date = ?
        """
        try:
            return await self._rows(sql, [trade_date])
        except duckdb.CatalogException:
            return []  # news feed not yet published to this snapshot
