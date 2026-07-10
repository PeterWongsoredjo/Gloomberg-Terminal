"""OB-09 rollup + OB-08 lifecycle against a live Postgres (skipped when the container is down).

Covers 05 5.7: the rollup is populated for a trade_date with LLM measures and carries its own
data_as_of (CT-011 for telemetry itself), and the prompt lifecycle promotes and deprecates cleanly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import asyncpg
import pytest

from app.agentic import ledger
from app.agentic.config import get_agentic_settings
from app.agentic.ids import new_ulid
from app.agentic.schemas import Ct009Artifact, Provenance, SentimentValue, Subject, TokenUsage, Window
from app.core.enums import QualityFlag
from app.eval import lifecycle
from app.observability import rollup

# a date with no leftover live-run data, so the rollup counts only what this test seeds
_TRADE_DATE = date(2020, 1, 2)


async def _pool_or_skip() -> asyncpg.Pool:
    """Opens the container Postgres pool, or skips when it is down."""
    settings = get_agentic_settings()
    try:
        return await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")


def _low_confidence_artifact() -> Ct009Artifact:
    """A CT-009 sentiment artifact flagged low-confidence, for a controlled rollup input."""
    return Ct009Artifact(
        artifact_id=new_ulid(),
        artifact_type="SENTIMENT",
        subject=Subject(security_id=1003, ticker="TLKM"),
        window=Window.model_validate({"from": _TRADE_DATE, "to": _TRADE_DATE}),
        value=SentimentValue(sentiment_score=0.1, sentiment_label="NEUTRAL", drivers=[], evidence_item_ids=[], self_confidence=0.3),
        confidence=0.3,
        provenance=Provenance(
            provider="groq", model="llama-3.3-70b", prompt_version="sent-v4",
            token_usage=TokenUsage(prompt=100, completion=20), generated_at=datetime.now(UTC),
        ),
        quality_flags=[QualityFlag.LLM_LOW_CONFIDENCE],
    )


async def test_rollup_populates_llm_measures_and_carries_data_as_of() -> None:
    pool = await _pool_or_skip()
    run_id = new_ulid()
    try:
        await ledger.setup(pool)
        await rollup.setup(pool)
        await ledger.start_run(pool, run_id=run_id, objective="daily_sentiment", trade_date=_TRADE_DATE, trace_id="tr-1")
        await ledger.write_artifact(pool, run_id, _low_confidence_artifact())
        await ledger.finish_run(pool, run_id=run_id, status="SUCCEEDED", abort_reason=None, consumed_tokens=120, consumed_iterations=1)
        await ledger.record_provider_health(
            pool, provider="groq", breaker_state="CLOSED", consecutive_failures=0, rpd_consumed=500, tpd_consumed=100000
        )

        row = await rollup.RollupBuilder().refresh(pool, _TRADE_DATE)
        assert row.data_as_of is not None  # CT-011: telemetry carries its own freshness
        assert row.llm_runs >= 1 and row.total_tokens >= 120
        assert row.low_confidence_artifact_count >= 1
        assert "LLM_LOW_CONFIDENCE" in row.quality_flags
        assert row.quota_pct_groq == pytest.approx(0.5)  # 500/1000 rpd ceiling
        assert row.breaker_state_groq == "CLOSED"

        stored = await rollup.read_rollup(pool, _TRADE_DATE)
        assert stored is not None and stored["session_state"]
    finally:
        await pool.execute("delete from agentic.agent_artifact where run_id = $1", run_id)
        await pool.execute("delete from agentic.agent_run where run_id = $1", run_id)
        await pool.execute("delete from observability.obs_telemetry_rollup where trade_date = $1", _TRADE_DATE)
        await pool.close()


async def test_prompt_lifecycle_promotes_and_deprecates() -> None:
    pool = await _pool_or_skip()
    try:
        await lifecycle.setup(pool)
        await lifecycle.register_draft(pool, objective="daily_sentiment", version="test-vX", content_sha256="aaa")
        await lifecycle.mark_evaluated(pool, objective="daily_sentiment", version="test-vX", mlflow_run_id="run-1")
        await lifecycle.promote_to_live(pool, objective="daily_sentiment", version="test-vX")
        assert (await lifecycle.live_versions(pool)).get("daily_sentiment") == "test-vX"

        await lifecycle.register_draft(pool, objective="daily_sentiment", version="test-vY", content_sha256="bbb")
        await lifecycle.promote_to_live(pool, objective="daily_sentiment", version="test-vY")
        assert (await lifecycle.live_versions(pool)).get("daily_sentiment") == "test-vY"
        assert await lifecycle.state_of(pool, objective="daily_sentiment", version="test-vX") == "DEPRECATED"
    finally:
        await pool.execute("delete from observability.prompt_version where version in ('test-vX', 'test-vY')")
        await pool.close()
