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
    if objective == "daily_sentiment":
        return set(value.get("evidence_item_ids", [])).issubset(set(draft["evidence_pool"]))
    if objective == "deep_extraction":
        return all((e.get("source_span") or "").strip() for e in value.get("events", []))
    return True


def _advisory_text(objective: str, value: dict[str, Any]) -> str:
    """The textual fields the non-advisory gate scans for an objective."""
    if objective == "daily_sentiment":
        return " ".join(value.get("drivers", []))
    if objective == "insight_synthesis":
        signals = " ".join(str(s.get("value", "")) for s in value.get("signals", []))
        return f"{value.get('headline', '')} {value.get('narrative', '')} {signals}"
    return " ".join((e.get("source_span") or "") for e in value.get("events", []))


def _context_consistent(objective: str, draft: dict[str, Any], corp_tickers: set[str]) -> bool:
    """A bearish read on a corporate-action move is a context contradiction."""
    if objective != "daily_sentiment":
        return True
    value = draft["value"]
    ticker = str(draft["subject"].get("ticker"))
    if ticker not in corp_tickers or value.get("sentiment_label") != "BEARISH":
        return True
    return not _PRICE_DROP.search(" ".join(value.get("drivers", [])))


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
        "entities_resolved": True,
        "non_advisory": not contains_advice(_advisory_text(objective, value)),
        "context_consistent": _context_consistent(objective, draft, corp_tickers),
        "confidence_calibrated": value_confidence(objective, value),
    }


_HARD_GATES = ("schema_valid", "grounded", "entities_resolved", "non_advisory", "context_consistent")


def _passed(checks: dict[str, Any]) -> bool:
    return all(bool(checks[g]) for g in _HARD_GATES)


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
    all_passed = all(d["passed"] for d in graded)
    if all_passed:
        verdict = "ACCEPT"
    elif can_retry:
        verdict = "OPTIMIZE"
    else:
        verdict = "REJECT"
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
