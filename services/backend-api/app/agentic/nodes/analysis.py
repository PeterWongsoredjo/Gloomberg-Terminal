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
from app.agentic.config import AgenticSettings
from app.agentic.nodes._common import get_deps, item_ids, newest, news_for_ticker, user_payload
from app.agentic.objectives import spec_for
from app.agentic.prompts.registry import PromptTemplate, get_prompt
from app.agentic.providers.base import ProviderError, ProviderRequest
from app.agentic.providers.ladder import ProviderLadder
from app.agentic.resolver import EntityResolver
from app.agentic.state import AgentState, budget_delta
from app.agentic.warehouse import GoldReader


def _article_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Only the fields the model needs to judge one article."""
    return {
        "item_id": item["item_id"],
        "title": item.get("title"),
        "summary": item.get("summary"),
        "published_at": str(item.get("published_at") or ""),
        "source": item.get("source"),
        "candidate_tickers": list(item.get("tickers") or []),
    }


def _batch_tasks(state: AgentState, batch_size: int, correction: list[str]) -> list[dict[str, Any]]:
    """One task per chunk of articles; each request returns a verdict for every article in it."""
    items = state["context"]["news_items"]
    tasks = []
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        payload = {"articles": [_article_payload(i) for i in chunk], "correction": correction}
        tasks.append(
            {
                "subject": {"ticker": None, "security_id": None},
                "user": user_payload(payload),
                "pool": sorted(item_ids(chunk)),
                "item_tickers": _item_tickers(chunk),
            }
        )
    return tasks


def _filing_payload(filing: dict[str, Any], char_cap: int) -> dict[str, Any]:
    """Only the fields the model needs to read one filing, its text capped."""
    text = str(filing["body"])
    return {
        "filing_id": str(filing["filing_id"]),
        "ticker": str(filing["ticker"]),
        "title": filing.get("title"),
        "filing_number": filing.get("filing_number"),
        "announced_at": str(filing.get("announced_at") or ""),
        "source_url": filing.get("source_url"),
        "text": text[:char_cap],
        "text_truncated": len(text) > char_cap,
    }


def _filing_tasks(
    state: AgentState, settings: AgenticSettings, correction: list[str]
) -> list[dict[str, Any]]:
    """One task per filing, so a long document never shares a request with another."""
    tasks = []
    for filing in state["context"]["documents"][: settings.dividend_filings_per_run]:
        payload = _filing_payload(filing, settings.dividend_filing_char_cap)
        payload["correction"] = correction
        tasks.append(
            {
                "subject": {"ticker": str(filing["ticker"]), "security_id": None},
                "user": user_payload(payload),
                "pool": [str(filing["filing_id"])],
            }
        )
    return tasks


def _item_tickers(chunk: list[dict[str, Any]]) -> dict[str, set[str]]:
    """The issuers each item already names on its own authority, keyed by item_id."""
    return {
        str(item["item_id"]): {str(t) for t in (item.get("tickers") or [])}
        for item in chunk
        if item.get("item_id") is not None
    }


def _subject_rows(state: AgentState) -> list[dict[str, Any]]:
    """Every resolved subject, carrying its Gold row when Gold happens to have one."""
    by_ticker = {str(row["ticker"]): row for row in state["context"]["market_context"]}
    return [
        by_ticker.get(ticker, {"ticker": ticker, "security_id": None, "market_context_available": False})
        for ticker in state["subject_universe"]
    ]


def _tasks(state: AgentState, settings: AgenticSettings) -> list[dict[str, Any]]:
    objective = state["objective"]
    context = state["context"]
    news_cap = settings.max_news_per_subject
    correction = (state["working"].get("evaluation") or {}).get("reasons", [])
    if objective == "article_sentiment":
        return _batch_tasks(state, settings.article_batch_size, correction)
    if objective == "dividend_extraction":
        return _filing_tasks(state, settings, correction)
    if objective == "deep_extraction":
        capped = newest(context["news_items"], news_cap)
        payload = {
            "documents": [_article_payload(i) for i in capped],
            "universe": state["subject_universe"],
            "correction": correction,
        }
        pool = sorted(item_ids(capped))
        return [{"subject": {"ticker": None, "security_id": None}, "user": user_payload(payload), "pool": pool}]

    tasks = []
    for row in _subject_rows(state):
        news = newest(news_for_ticker(context["news_items"], str(row["ticker"])), news_cap)
        payload = {
            "subject": {"ticker": row["ticker"], "security_id": row.get("security_id")},
            "market_context": row,
            "news_items": news,
            "correction": correction,
        }
        tasks.append(
            {
                "subject": {"ticker": row["ticker"], "security_id": row.get("security_id")},
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
        estimated_tokens=len(user) // 3 + len(prompt.system_contract) // 3 + prompt.max_output_tokens,
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


def _split_tickers(
    entries: list[dict[str, Any]], resolver: EntityResolver, authoritative: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keeps the issuers the registry knows or the item itself named, drops the invented ones."""
    kept, dropped = [], []
    for entry in entries:
        ticker = str(entry.get("ticker") or "")
        if resolver.is_known(ticker) or ticker in authoritative:
            kept.append(entry)
        else:
            dropped.append(ticker)
    return kept, dropped


def _fan_out(result: dict[str, Any], task: dict[str, Any], resolver: EntityResolver) -> list[dict[str, Any]]:
    """Splits one batched response into a draft per article, so one bad verdict cannot sink the rest."""
    batch = result["value"] or {}
    base = {
        "artifact_type": "ARTICLE_SENTIMENT",
        "provider": result["provider"],
        "model": result["model"],
        "batch_id": task["batch_id"],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    verdicts = batch.get("verdicts") or []
    if not verdicts:
        return [{**base, "subject": task["subject"], "value": None, "invalid": True,
                 "evidence_pool": task["pool"], "prompt_tokens": result["prompt_tokens"],
                 "completion_tokens": result["completion_tokens"]}]

    item_tickers: dict[str, set[str]] = task.get("item_tickers") or {}
    drafts = []
    for index, verdict in enumerate(verdicts):
        authoritative = item_tickers.get(str(verdict.get("item_id")), set())
        kept, dropped = _split_tickers(
            verdict.get("ticker_sentiments") or [], resolver, authoritative
        )
        drafts.append(
            {
                **base,
                "subject": {"ticker": None, "security_id": None},
                "value": {**verdict, "ticker_sentiments": kept, "dropped_tickers": dropped},
                "invalid": False,
                "evidence_pool": [str(verdict.get("item_id"))],
                "batch_pool": task["pool"],
                "prompt_tokens": result["prompt_tokens"] if index == 0 else 0,
                "completion_tokens": result["completion_tokens"] if index == 0 else 0,
            }
        )
    return drafts


async def run_analysis(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    objective = state["objective"]
    prompt = get_prompt(objective)
    spec = spec_for(objective)
    ladder = deps.ladder_for(objective)
    batched = objective == "article_sentiment"
    resolver = await EntityResolver.from_registry(deps.pg_pool, GoldReader(deps.duckdb_ro)) if batched else None

    drafts: list[dict[str, Any]] = []
    tokens = 0
    async with deps.tracer.span(objective, state["run_id"]) as span:
        try:
            for index, task in enumerate(_tasks(state, deps.settings)):
                task["batch_id"] = f"{state['run_id']}:{index}"
                result = await _infer(ladder, prompt, spec.response_model, task["user"])
                tokens += result["prompt_tokens"] + result["completion_tokens"]
                if resolver is not None:
                    drafts.extend(_fan_out(result, task, resolver))
                    continue
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
