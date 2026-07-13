"""Read-only DuckDB Gold access for facts not projected to Postgres.

Candles, news, the insight narrative, and the headline index live only in Gold. Every query runs
on its own cursor inside a worker thread so a blocking scan never stalls the event loop. A missing
snapshot or table yields empty, never a crash.
"""

from __future__ import annotations

import asyncio
from typing import Any

import duckdb


class ServingGoldReader:
    """The Gold-side queries the serving endpoints need."""

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
                if cursor.description is None:
                    return []
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            finally:
                cursor.close()

        return await asyncio.to_thread(_run)

    async def _row(self, sql: str, params: list[Any]) -> dict[str, Any] | None:
        rows = await self._rows(sql, params)
        return rows[0] if rows else None

    async def candles(self, ticker: str, limit_days: int) -> list[dict[str, Any]]:
        """Daily OHLC bars for a ticker, most recent window first then returned oldest-first."""
        sql = """
            select t.trade_date, t.open_idr, t.high_idr, t.low_idr, t.close_idr,
                   t.close_adj_idr, t.volume_shares, t.price_series_integrity, t.dq_flags
            from fct_daily_trade t
            join dim_security s on t.security_id = s.security_id and s.is_current
            where s.ticker = ?
            order by t.trade_date desc
            limit ?
        """
        rows = await self._safe(sql, [ticker, limit_days])
        return list(reversed(rows))

    async def news(self, limit: int, before: str | None) -> list[dict[str, Any]]:
        """Newest headlines, optionally paged behind a (published_at, item_id) cursor."""
        params: list[Any] = []
        clause = ""
        if before is not None:
            clause = "where (published_at || '|' || item_id) < ?"
            params.append(before)
        params.append(limit)
        sql = f"""
            select item_id, trade_date, source, lang, title, summary, url, published_at, tickers,
                   (published_at || '|' || item_id) as _cursor
            from fct_news_item
            {clause}
            order by published_at desc, item_id desc
            limit ?
        """
        return await self._safe(sql, params)

    async def insight(self, ticker: str) -> dict[str, Any] | None:
        """The latest insight narrative for a ticker; the agent writes it through dbt, never here."""
        sql = """
            select artifact_id, ticker, trade_date, prompt_version, headline, narrative,
                   contradictions, confidence, provider, model, generated_at, dq_flags
            from fct_insight
            where ticker = ?
            order by trade_date desc, generated_at desc
            limit 1
        """
        try:
            return await self._row(sql, [ticker])
        except duckdb.CatalogException:
            return None  # insight facts not yet published to this snapshot

    async def headline_index(self, index_id: str) -> dict[str, Any] | None:
        """The latest level for one index, for the session strip."""
        sql = """
            select index_id, trade_date, close_level, change_level
            from fct_index_level
            where index_id = ?
            order by trade_date desc
            limit 1
        """
        return await self._row(sql, [index_id])

    async def latest_trade_date(self) -> str | None:
        """The most recent trade_date present in Gold, to anchor the calendar."""
        row = await self._row("select max(trade_date) as d from fct_daily_trade", [])
        return None if row is None or row["d"] is None else str(row["d"])

    async def _safe(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        """A read that tolerates a not-yet-published table, returning empty."""
        try:
            return await self._rows(sql, params)
        except duckdb.CatalogException:
            return []
