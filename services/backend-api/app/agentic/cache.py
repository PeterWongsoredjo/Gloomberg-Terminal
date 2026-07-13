"""
Hashes a run's parameters, if in the lifespan the same run is hit, then
the LLM just finalizes, no need to run it again for the same result
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg


def compute_key(
    *,
    objective: str,
    prompt_version: str,
    provider: str,
    subject_universe: list[str],
    trade_date: str,
    news_fingerprint: str,
    market_fingerprint: str,
) -> str:
    payload = "|".join(
        [
            objective,
            prompt_version,
            provider,
            ",".join(sorted(subject_universe)),
            trade_date,
            news_fingerprint,
            market_fingerprint,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint(rows: list[dict[str, Any]]) -> str:
    """A stable content hash of a list of context rows, order-independent."""
    canonical = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheHit:
    """Cached artifacts plus how old the entry is, for the caller's age threshold."""

    artifacts: list[dict[str, Any]]
    age_seconds: float


async def get(pool: asyncpg.Pool, cache_key: str) -> CacheHit | None:
    row = await pool.fetchrow(
        "select artifacts, stored_at from agentic.artifact_cache where cache_key = $1",
        cache_key,
    )
    if row is None:
        return None
    age = (datetime.now(UTC) - row["stored_at"]).total_seconds()
    return CacheHit(artifacts=json.loads(row["artifacts"]), age_seconds=age)


async def put(pool: asyncpg.Pool, cache_key: str, artifacts: list[dict[str, Any]]) -> None:
    await pool.execute(
        """
        insert into agentic.artifact_cache (cache_key, artifacts, stored_at)
        values ($1, $2, now())
        on conflict (cache_key) do update set artifacts = excluded.artifacts, stored_at = now()
        """,
        cache_key,
        json.dumps(artifacts),
    )
