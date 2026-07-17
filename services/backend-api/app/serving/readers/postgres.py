"""
Read-only Postgres access to the agg_* projections and the agentic ledger.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger("gloomberg.serving.pg")


def _json_list(raw: Any) -> list[Any]:
    """A jsonb column as a real list, asyncpg hands it back as a string."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


class ServingPostgresReader:
    """The Postgres-side queries the serving endpoints need."""

    def __init__(self, pool: asyncpg.Pool | None) -> None:
        self._pool = pool

    async def live_tape(self) -> list[dict[str, Any]]:
        return await self._fetch('select * from public."agg_live_tape" order by ticker')

    async def securities_page(self, after_ticker: str | None, after_id: int | None, limit: int) -> list[dict[str, Any]]:
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
        return await self._fetchrow('select * from public."agg_security_snapshot" where ticker = $1', ticker)

    async def sentiment_rows(self) -> list[dict[str, Any]]:
        return await self._fetch('select * from public."agg_sentiment_matrix"')

    async def tape_row_for(self, ticker: str) -> dict[str, Any] | None:
        return await self._fetchrow('select * from public."agg_live_tape" where ticker = $1', ticker)

    async def sentiment_for(self, ticker: str) -> dict[str, Any] | None:
        return await self._fetchrow('select * from public."agg_sentiment_matrix" where ticker = $1', ticker)

    async def intraday_news(self, limit: int, before: tuple[datetime, str] | None) -> list[dict[str, Any]]:
        """Freshly polled headlines not yet in Gold, same cursor shape as the Gold read."""
        if before is None:
            sql = """
                select item_id, trade_date, source, lang, title, summary, url, published_at, tickers
                from intraday.news_item
                order by published_at desc, item_id desc
                limit $1
            """
            return await self._fetch(sql, limit)
        sql = """
            select item_id, trade_date, source, lang, title, summary, url, published_at, tickers
            from intraday.news_item
            where published_at < $1 or (published_at = $1 and item_id < $2)
            order by published_at desc, item_id desc
            limit $3
        """
        return await self._fetch(sql, before[0], before[1], limit)

    async def latest_intraday_sentiment(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """The newest in-session sentiment row per ticker, shaped like the Gold read."""
        if not tickers:
            return {}
        sql = """
            select distinct on (ticker)
                   ticker, sentiment_score, sentiment_label, confidence, artifact_id,
                   provider, model, prompt_version, generated_at, evidence_item_ids
            from intraday.sentiment
            where ticker = any($1)
            order by ticker, generated_at desc
        """
        rows = await self._fetch(sql, tickers)
        return {str(r["ticker"]): r for r in rows}

    async def latest_intraday_insight(self, ticker: str) -> dict[str, Any] | None:
        """The newest in-session insight row for one ticker, shaped like the Gold read."""
        sql = """
            select artifact_id, ticker, trade_date, prompt_version, headline, narrative,
                   contradictions, confidence, provider, model, generated_at,
                   quality_flags as dq_flags
            from intraday.insight
            where ticker = $1
            order by generated_at desc
            limit 1
        """
        row = await self._fetchrow(sql, ticker)
        if row is None:
            return None
        row["contradictions"] = _json_list(row.get("contradictions"))
        row["dq_flags"] = _json_list(row.get("dq_flags"))
        return row

    async def insight_provenance(self, artifact_id: str) -> dict[str, Any] | None:
        sql = """
            select r.run_id, r.trace_id, r.consumed_iterations, r.status
            from agentic.agent_artifact a
            join agentic.agent_run r on a.run_id = r.run_id
            where a.artifact_id = $1
        """
        return await self._fetchrow(sql, artifact_id)

    async def artifact_provenance(self, artifact_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Run and trace ids for a batch of artifacts, keyed by artifact_id."""
        if not artifact_ids:
            return {}
        sql = """
            select a.artifact_id, r.run_id, r.trace_id
            from agentic.agent_artifact a
            join agentic.agent_run r on a.run_id = r.run_id
            where a.artifact_id = any($1)
        """
        rows = await self._fetch(sql, artifact_ids)
        return {str(r["artifact_id"]): r for r in rows}

    async def run(self, run_id: str) -> dict[str, Any] | None:
        sql = """
            select run_id, objective, trade_date, status, abort_reason,
                   consumed_iterations, trace_id, started_at, ended_at
            from agentic.agent_run where run_id = $1
        """
        return await self._fetchrow(sql, run_id)

    async def run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
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
