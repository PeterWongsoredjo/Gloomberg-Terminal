"""
evaluates what an agent returns, this way we can block hallucination
and every negative that an agent might do
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.budget import iterations_left, tokens_left
from app.agentic.nodes._common import contains_advice, get_deps, value_confidence
from app.agentic.objectives import spec_for
from app.agentic.state import AgentState

_PRICE_DROP = re.compile(r"crash|plunge|anjlok|jatuh|merosot|tumbang|sell-?off|collaps", re.IGNORECASE)


def _corp_action_tickers(state: AgentState) -> set[str]:
    affecting = {"STOCK_SPLIT", "REVERSE_SPLIT", "RIGHTS_ISSUE", "BONUS_SHARES", "STOCK_DIVIDEND"}
    return {
        str(a["ticker"])
        for a in state["context"]["corporate_actions"]
        if a.get("event_type") in affecting and a.get("ticker")
    }


def _grounded(objective: str, draft: dict[str, Any]) -> bool:
    """Whether the draft cites only supplied evidence for its objective."""
    value = draft["value"]
    kind = spec_for(objective).artifact_type
    if kind == "ARTICLE_SENTIMENT":
        return str(value.get("item_id")) in set(draft.get("batch_pool") or draft["evidence_pool"])
    if kind == "SENTIMENT":
        return set(value.get("evidence_item_ids", [])).issubset(set(draft["evidence_pool"]))
    if kind == "EXTRACTION":
        return all((e.get("source_span") or "").strip() for e in value.get("events", []))
    return True


def _primary_ticker(value: dict[str, Any]) -> str | None:
    """The issuer an article is mainly about, when it has one."""
    entries = value.get("ticker_sentiments") or []
    primary = [e for e in entries if e.get("relevance") == "PRIMARY"]
    chosen = primary or entries
    return str(chosen[0]["ticker"]) if chosen else None


def _entities_resolved(objective: str, value: dict[str, Any]) -> bool:
    """A directional read has to be about somebody; a neutral macro note need not be."""
    if spec_for(objective).artifact_type != "ARTICLE_SENTIMENT":
        return True
    if value.get("ticker_sentiments"):
        return True
    return value.get("sentiment_label") == "NEUTRAL"


def _advisory_text(objective: str, value: dict[str, Any]) -> str:
    """Every free-text field the non-advisory gate scans for an objective."""
    kind = spec_for(objective).artifact_type
    if kind in ("SENTIMENT", "ARTICLE_SENTIMENT"):
        return _joined([*value.get("drivers", []), value.get("rationale")])
    if kind == "INSIGHT":
        return _joined(
            [
                value.get("headline"),
                value.get("narrative"),
                *(s.get("value") for s in value.get("signals", [])),
                *value.get("watchpoints", []),
            ]
        )
    return _joined([e.get("source_span") for e in value.get("events", [])])


def _joined(parts: list[Any]) -> str:
    """One scannable string, skipping the fields this artifact did not fill in."""
    return " ".join(str(p) for p in parts if p)


def _context_consistent(objective: str, draft: dict[str, Any], corp_tickers: set[str]) -> bool:
    """A bearish read on a corporate-action move is a context contradiction."""
    kind = spec_for(objective).artifact_type
    if kind not in ("SENTIMENT", "ARTICLE_SENTIMENT"):
        return True
    value = draft["value"]
    ticker = _primary_ticker(value) if kind == "ARTICLE_SENTIMENT" else str(draft["subject"].get("ticker"))
    if ticker not in corp_tickers or value.get("sentiment_label") != "BEARISH":
        return True
    return not _PRICE_DROP.search(" ".join([*value.get("drivers", []), value.get("rationale") or ""]))


def _checks(objective: str, draft: dict[str, Any], corp_tickers: set[str]) -> dict[str, Any]:
    schema_valid = not draft["invalid"] and draft["value"] is not None
    if not schema_valid:
        return {
            "schema_valid": False,
            "grounded": False,
            "entities_resolved": False,
            "non_advisory": False,
            "context_consistent": False,
            "confidence_calibrated": 0.0,
        }
    value = draft["value"]
    return {
        "schema_valid": True,
        "grounded": _grounded(objective, draft),
        "entities_resolved": _entities_resolved(objective, value),
        "non_advisory": not contains_advice(_advisory_text(objective, value)),
        "context_consistent": _context_consistent(objective, draft, corp_tickers),
        "confidence_calibrated": value_confidence(objective, value),
    }


_HARD_GATES = ("schema_valid", "grounded", "entities_resolved", "non_advisory", "context_consistent")


def _passed(checks: dict[str, Any]) -> bool:
    return all(bool(checks[g]) for g in _HARD_GATES)


def _verdict(objective: str, graded: list[dict[str, Any]], can_retry: bool) -> str:
    """Whether the run retries, accepts what it has, or gives up."""
    passed = [d for d in graded if d["passed"]]
    if len(passed) == len(graded):
        return "ACCEPT"
    if spec_for(objective).artifact_type == "ARTICLE_SENTIMENT" and passed:
        return "ACCEPT"
    return "OPTIMIZE" if can_retry else "REJECT"


async def evaluate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    objective = state["objective"]
    corp_tickers = _corp_action_tickers(state)
    drafts = state["working"]["draft_artifacts"]

    async with deps.tracer.span("evaluate", state["run_id"]):
        graded: list[dict[str, Any]] = []
        reasons: list[str] = []
        for draft in drafts:
            checks = _checks(objective, draft, corp_tickers)
            passed = _passed(checks)
            graded.append({**draft, "checks": checks, "passed": passed})
            if not passed:
                reasons.extend(g for g in _HARD_GATES if not checks[g])

    can_retry = iterations_left(state["budget"]) and tokens_left(state["budget"])
    verdict = _verdict(objective, graded, can_retry)
    return {
        "working": {
            **state["working"],
            "draft_artifacts": graded,
            "evaluation": {"verdict": verdict, "reasons": sorted(set(reasons))},
        }
    }


def route_after_evaluate(state: AgentState) -> str:
    verdict = (state["working"].get("evaluation") or {}).get("verdict")
    return "optimize" if verdict == "OPTIMIZE" else "finalize"
