"""
Finalize is the main coordinator node for the cleanup phase. It:
1. filters out any draft that failed the evaluation step
2. Wraps every accepted draft into a confidence-gated envelope.
3. writes the final artifacts to the ledger (SUCCEEDED or DEGRADED)
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any, cast

import asyncpg
from langchain_core.runnables import RunnableConfig

from app.agentic import cache, intraday, ledger, rollup
from app.agentic.config import AgenticSettings
from app.agentic.confidence import apply_gate
from app.agentic.deps import GraphDeps
from app.agentic.ids import new_ulid
from app.agentic.nodes._common import get_deps, value_confidence
from app.agentic.objectives import spec_for_type
from app.agentic.schemas import (
    ArticleSentimentValue,
    Ct009Artifact,
    CashDividendValue,
    ExtractionValue,
    InsightValue,
    Provenance,
    SentimentValue,
    Subject,
    TokenUsage,
    Window,
)
from app.agentic.state import AgentState

logger = logging.getLogger(__name__)


async def _roll_up_tickers(pool: asyncpg.Pool, state: AgentState) -> None:
    """Derives per-ticker sentiment from the day's scored articles, spending no tokens."""
    trade_date = date.fromisoformat(state["trade_date"])
    rows = await intraday.rollup_source(pool, state["trade_date"])
    rolled = rollup.roll_up(rows, rollup.day_end(trade_date))
    if not rolled:
        return
    tuples = rollup.upsert_tuples(
        rolled,
        trade_date,
        state["run_id"],
        state.get("trace_id"),
        datetime.now(UTC),
    )
    await intraday.project(pool, state, tuples)


async def _project_intraday(
    deps: GraphDeps, state: AgentState, artifacts: list[dict[str, Any]], batch_ids: dict[str, str]
) -> None:
    """Lands the serving projections, the next scheduled run retries on failure."""
    objective = state["objective"]
    if deps.pg_pool is None:
        return
    try:
        if objective == "article_sentiment":
            await intraday.project_article_sentiment(deps.pg_pool, state, artifacts, batch_ids)
            await _roll_up_tickers(deps.pg_pool, state)
        elif objective == "dividend_extraction":
            await intraday.project_cash_dividend(deps.pg_pool, state, artifacts)
        elif objective in intraday.INSIGHT_PROJECTED:
            await intraday.project_insight(deps.pg_pool, state, artifacts)
    except asyncpg.PostgresError:
        logger.warning("intraday projection write failed for run %s", state["run_id"], exc_info=True)


def _build_artifact(draft: dict[str, Any], state: AgentState, settings: AgenticSettings) -> Ct009Artifact:
    spec = spec_for_type(draft["artifact_type"])
    value = cast(
        SentimentValue | ArticleSentimentValue | ExtractionValue | InsightValue | CashDividendValue,
        spec.value_model.model_validate(draft["value"]),
    )
    confidence = value_confidence(spec.objective, draft["value"])
    flags = apply_gate(spec.objective, confidence, [], settings)
    return Ct009Artifact(
        artifact_id=new_ulid(),
        artifact_type=spec.artifact_type,
        subject=Subject(**draft["subject"]),
        window=Window.model_validate({"from": state["trade_date"], "to": state["trade_date"]}),
        value=value,
        confidence=confidence,
        provenance=Provenance(
            provider=draft["provider"],
            model=draft["model"],
            prompt_version=state["working"]["prompt_version"],
            trace_id=state.get("trace_id"),
            input_source_refs=draft["evidence_pool"],
            token_usage=TokenUsage(prompt=draft["prompt_tokens"], completion=draft["completion_tokens"]),
            generated_at=draft["generated_at"],
            loop_iterations=state["budget"]["consumed_iterations"],
        ),
        quality_flags=flags,
    )


async def _persist(deps: GraphDeps, state: AgentState, new_dicts: list[dict[str, Any]]) -> None:
    """Writes every artifact to the ledger and records the run's terminal status."""
    if deps.pg_pool is None:
        return
    for artifact in [*state.get("artifacts", []), *new_dicts]:
        await ledger.write_artifact(deps.pg_pool, state["run_id"], Ct009Artifact.model_validate(artifact))
    if new_dicts:
        await cache.put(deps.pg_pool, state["working"]["cache_key"], new_dicts)


async def _record_health(deps: GraphDeps) -> None:
    """Persists each provider's breaker state and daily consumption after the run."""
    if deps.pg_pool is None:
        return
    for name, slot in deps.slots.items():
        requests, tokens = deps.quota.consumption(name) if deps.quota is not None else (0, 0)
        await ledger.record_provider_health(
            deps.pg_pool,
            provider=name,
            breaker_state=slot.breaker.state,
            consecutive_failures=slot.breaker.failure_count,
            rpd_consumed=requests,
            tpd_consumed=tokens,
        )


async def finalize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    settings = deps.settings
    accepted = [d for d in state["working"].get("draft_artifacts", []) if d.get("passed")]

    async with deps.tracer.span("finalize", state["run_id"]) as span:
        new_dicts = [_build_artifact(d, state, settings).model_dump(by_alias=True, mode="json") for d in accepted]
        total = len(state.get("artifacts", [])) + len(new_dicts)
        idle = bool(state["working"].get("nothing_to_do"))
        status = "DEGRADED" if state.get("abort_reason") or (total == 0 and not idle) else "SUCCEEDED"
        span.set_output({"artifacts": total, "status": status})

        batch_ids = {
            str((d.get("value") or {}).get("item_id")): str(d["batch_id"])
            for d in accepted
            if d.get("batch_id") and (d.get("value") or {}).get("item_id")
        }
        await _persist(deps, state, new_dicts)
        await _project_intraday(deps, state, [*state.get("artifacts", []), *new_dicts], batch_ids)
        await _record_health(deps)
        if deps.pg_pool is not None:
            await ledger.finish_run(
                deps.pg_pool,
                run_id=state["run_id"],
                status=status,
                abort_reason=state.get("abort_reason"),
                consumed_tokens=state["budget"]["consumed_tokens"],
                consumed_iterations=state["budget"]["consumed_iterations"],
            )

    return {"artifacts": new_dicts, "status": status}
