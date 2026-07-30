"""
Market context straight from the live tape projection in Postgres.

The end-of-day conclusion needs the session that just closed. The published Gold
snapshot is opened once at startup and keeps its old file handle across a promote,
so the projection is the reliable source for today's closes, not the warehouse.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_TAPE_SQL = """
select security_id, ticker, board, is_fca, sector_idxic, special_notation,
       close_adj_idr, change_pct as daily_return, net_foreign_idr,
       price_series_integrity, dq_flags
from public."agg_live_tape"
where ticker = any($1)
order by ticker
"""


async def market_context(pool: asyncpg.Pool, tickers: list[str]) -> list[dict[str, Any]]:
    """Today's closes and foreign flow for the subjects, empty when the tape is unreachable."""
    if not tickers:
        return []
    try:
        rows = await pool.fetch(_TAPE_SQL, tickers)
    except asyncpg.PostgresError:
        logger.warning("live tape unreachable, market context empty", exc_info=True)
        return []
    return [dict(r) for r in rows]
