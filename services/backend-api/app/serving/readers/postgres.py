"""Read-only Postgres access to the agg_* projections and the agentic ledger.

These are the synced serving projections plus the run ledger. Reads use the async pool; every
query is parameterized. A down store surfaces as empty, not a 500.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("gloomberg.serving.pg")


class ServingPostgresReader:
    """The Postgres-side queries the serving endpoints need."""

    def __init__(self, pool: asyncpg.Pool | None) -> None:
        self._pool = pool

    async def live_tape(self) -> list[dict[str, Any]]:
        """Every current tape row; the full snapshot the REST and WS paths both start from."""
        return await self._fetch('select * from public."agg_live_tape" order by ticker')

    async def securities_page(self, after_ticker: str | None, after_id: int | None, limit: int) -> list[dict[str, Any]]:
        """One keyset page of the universe, ordered by (ticker, security_id), limit+1 for more-detection."""
        if after_ticker is None:
            sql = (
                'select security_id, ticker, isin, board, sector_idxic, is_fca '
                'from public."agg_security_snapshot" order by ticker, security_id limit $1'
            )
            return await self._fetch(sql, limit + 1)
        sql = (
            'select security_id, ticker, isin, board, sector_idxic, is_fca '
            'from public."agg_security_snapshot" '
            'where (ticker, security_id) > ($1, $2) order by ticker, security_id limit $3'
        )
        return await self._fetch(sql, after_ticker, after_id, limit + 1)

    async def security_snapshot(self, ticker: str) -> dict[str, Any] | None:
        """The Insight Panel header row for one ticker."""
        return await self._fetchrow('select * from public."agg_security_snapshot" where ticker = $1', ticker)

    async def sentiment_rows(self) -> list[dict[str, Any]]:
        """Every current per-security sentiment row; the endpoint aggregates to the matrix grid."""
        return await self._fetch('select * from public."agg_sentiment_matrix"')

    async def tape_row_for(self, ticker: str) -> dict[str, Any] | None:
        """One tape row, to derive the flow/price signal chips on the Insight Panel."""
        return await self._fetchrow('select * from public."agg_live_tape" where ticker = $1', ticker)

    async def sentiment_for(self, ticker: str) -> dict[str, Any] | None:
        """One security's current sentiment, to derive the sentiment signal chip."""
        return await self._fetchrow('select * from public."agg_sentiment_matrix" where ticker = $1', ticker)

    async def insight_provenance(self, artifact_id: str) -> dict[str, Any] | None:
        """Trace id, loop count, and run status behind an insight artifact."""
        sql = """
            select r.trace_id, r.consumed_iterations, r.status
            from agentic.agent_artifact a
            join agentic.agent_run r on a.run_id = r.run_id
            where a.artifact_id = $1
        """
        return await self._fetchrow(sql, artifact_id)

    async def run(self, run_id: str) -> dict[str, Any] | None:
        """A run row, for the reasoning trace."""
        sql = """
            select run_id, objective, trade_date, status, abort_reason,
                   consumed_iterations, trace_id, started_at, ended_at
            from agentic.agent_run where run_id = $1
        """
        return await self._fetchrow(sql, run_id)

    async def run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """The artifacts a run produced, to describe what it concluded."""
        sql = """
            select artifact_id, artifact_type, ticker, confidence, quality_flags, provider
            from agentic.agent_artifact where run_id = $1 order by generated_at
        """
        return await self._fetch(sql, run_id)

    async def _fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if self._pool is None:
            return []
        try:
            rows = await self._pool.fetch(sql, *args)
        except asyncpg.PostgresError as exc:
            logger.warning("serving pg read failed, degrading to empty: %s", exc)
            return []
        return [dict(r) for r in rows]

    async def _fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = await self._fetch(sql, *args)
        return rows[0] if rows else None
