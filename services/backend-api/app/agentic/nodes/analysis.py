"""
Runs the analysis on news, including:
1. Sentiment analysis
2. Deep extraction
3. Synthesize insight
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from app.agentic import cache
from app.agentic.nodes._common import get_deps, item_ids, news_for_ticker, user_payload
from app.agentic.objectives import spec_for
from app.agentic.prompts.registry import PromptTemplate, get_prompt
from app.agentic.providers.base import ProviderError, ProviderRequest
from app.agentic.providers.ladder import ProviderLadder
from app.agentic.state import AgentState, budget_delta


def _tasks(state: AgentState, prompt: PromptTemplate) -> list[dict[str, Any]]:
    objective = state["objective"]
    context = state["context"]
    correction = (state["working"].get("evaluation") or {}).get("reasons", [])
    if objective == "deep_extraction":
        payload = {
            "documents": context["news_items"],
            "universe": state["subject_universe"],
            "correction": correction,
        }
        pool = sorted(item_ids(context["news_items"]))
        return [{"subject": {"ticker": None, "security_id": None}, "user": user_payload(payload), "pool": pool}]

    tasks = []
    for row in context["market_context"]:
        news = news_for_ticker(context["news_items"], str(row["ticker"]))
        payload = {
            "subject": {"ticker": row["ticker"], "security_id": row["security_id"]},
            "market_context": row,
            "news_items": news,
            "correction": correction,
        }
        tasks.append(
            {
                "subject": {"ticker": row["ticker"], "security_id": row["security_id"]},
                "user": user_payload(payload),
                "pool": sorted(item_ids(news)),
            }
        )
    return tasks


async def _infer(
    ladder: ProviderLadder, prompt: PromptTemplate, schema: type[BaseModel], user: str
) -> dict[str, Any]:
    """Runs one request and normalizes the outcome into a draft-ready dict."""
    request = ProviderRequest(
        objective=prompt.objective,
        prompt_version=prompt.version,
        system=prompt.system_contract,
        user=user,
        response_model=schema,
        temperature=prompt.temperature,
        seed=prompt.seed,
        max_output_tokens=prompt.max_output_tokens,
    )
    response = await ladder.complete(request)
    value: dict[str, Any] | None = None
    if response.parsed is not None:
        try:
            value = schema.model_validate(response.parsed).model_dump(mode="json")
        except ValidationError:
            value = None
    return {
        "value": value,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "provider": response.provider,
        "model": response.model,
    }


async def _tier2_cache(deps: Any, state: AgentState) -> list[dict[str, Any]] | None:
    if deps.pg_pool is None:
        return None
    hit = await cache.get(deps.pg_pool, state["working"]["cache_key"])
    if hit is None or hit.age_seconds > deps.settings.cache_staleness_budget_hours * 3600:
        return None
    stale = []
    for artifact in hit.artifacts:
        flags = list(artifact.get("quality_flags", []))
        if "STALE" not in flags:
            flags.append("STALE")
        stale.append({**artifact, "quality_flags": flags})
    return stale


async def run_analysis(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    objective = state["objective"]
    prompt = get_prompt(objective)
    spec = spec_for(objective)
    schema = spec.value_model
    ladder = deps.ladder_for(objective)

    drafts: list[dict[str, Any]] = []
    tokens = 0
    async with deps.tracer.span(objective, state["run_id"]) as span:
        try:
            for task in _tasks(state, prompt):
                result = await _infer(ladder, prompt, schema, task["user"])
                tokens += result["prompt_tokens"] + result["completion_tokens"]
                drafts.append(
                    {
                        "subject": task["subject"],
                        "artifact_type": spec.artifact_type,
                        "value": result["value"],
                        "invalid": result["value"] is None,
                        "provider": result["provider"],
                        "model": result["model"],
                        "prompt_tokens": result["prompt_tokens"],
                        "completion_tokens": result["completion_tokens"],
                        "evidence_pool": task["pool"],
                        "generated_at": datetime.now(UTC).isoformat(),
                    }
                )
        except ProviderError as exc:
            span.set_error(str(exc))
            stale = await _tier2_cache(deps, state)
            if stale is not None:
                return {"artifacts": stale, "working": {**state["working"], "draft_artifacts": []}}
            return {
                "working": {**state["working"], "draft_artifacts": []},
                "abort_reason": f"all providers down: {exc}",
                "budget": budget_delta(tokens=tokens),
            }

    return {
        "working": {**state["working"], "draft_artifacts": drafts},
        "budget": budget_delta(tokens=tokens),
    }


async def sentiment_analyze(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    return await run_analysis(state, config)


async def deep_extract(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Extracts structured events from documents (Gemini primary)."""
    return await run_analysis(state, config)


async def synthesize_insight(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    return await run_analysis(state, config)


def after_analysis(state: AgentState) -> str:
    if state.get("abort_reason"):
        return "finalize"
    if not state["working"]["draft_artifacts"]:
        return "finalize"
    return "evaluate"
