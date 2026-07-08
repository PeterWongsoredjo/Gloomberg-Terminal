"""Small shared helpers the graph nodes lean on: deps access, grounding, advice detection."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.deps import GraphDeps

# prescriptive language the non-advisory gate rejects, in English and Indonesian
_ADVICE = re.compile(
    r"\b(buy|sell|hold|accumulate|overweight|underweight|target\s*price|price\s*target|"
    r"take\s*profit|cut\s*loss|beli|jual|akumulasi|rekomendasi)\b",
    re.IGNORECASE,
)


def get_deps(config: RunnableConfig) -> GraphDeps:
    """Pulls the injected dependency bundle out of the run config."""
    deps = config["configurable"]["deps"]
    assert isinstance(deps, GraphDeps)
    return deps


def contains_advice(text: str) -> bool:
    """True when the text uses prescriptive buy/sell/target language."""
    return bool(_ADVICE.search(text or ""))


def news_for_ticker(news_items: list[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    """News items that name the ticker, or all items when none are ticker-tagged."""
    tagged = [n for n in news_items if ticker in (n.get("tickers") or [])]
    return tagged if tagged else news_items


def item_ids(news_items: list[dict[str, Any]]) -> set[str]:
    """The set of supplied news item_ids, for the grounding check."""
    return {str(n["item_id"]) for n in news_items if n.get("item_id") is not None}


def user_payload(payload: dict[str, Any]) -> str:
    """Serializes a node's input context to compact JSON for the model."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def value_confidence(objective: str, value: dict[str, Any]) -> float:
    """The artifact-level confidence for an objective's value block."""
    if objective == "daily_sentiment":
        return float(value.get("self_confidence", 0.0))
    if objective == "insight_synthesis":
        return float(value.get("confidence", 0.0))
    events = value.get("events") or []
    return min((float(e.get("confidence", 0.0)) for e in events), default=0.4)
