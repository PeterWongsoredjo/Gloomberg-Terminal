"""Small shared helpers the graph nodes lean on: deps access, grounding, advice detection."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agentic.deps import GraphDeps
from app.agentic.objectives import spec_for

# telling someone to trade, in English and Indonesian
_PRESCRIPTIVE = (
    r"buy|sell|hold|accumulate|overweight|underweight|target\s*price|price\s*target|"
    r"take\s*profit|cut\s*loss|beli|jual|akumulasi|rekomendasi|disarankan|sebaiknya|"
    r"layak\s*(?:beli|dibeli|dikoleksi)"
)
# how the market is described, not what anyone should do about it
_DESCRIPTIVE = (
    r"(?:foreign\s*)?net\s*(?:foreign\s*)?(?:buy|sell)(?:ing|er|ers)?|"
    r"(?:beli|jual)\s*bersih|net\s*asing|asing\s*net\s*(?:buy|sell)|"
    r"(?:minat|aksi|tekanan|volume|nilai|daya|kekuatan|arus)\s*(?:beli|jual)|"
    r"buying\s*interest|selling\s*pressure|buy\s*side|sell\s*side"
)
# the descriptive branch runs first, so it eats the wording before the advice branch sees it
_ADVICE = re.compile(rf"\b(?:(?P<flow>{_DESCRIPTIVE})|(?P<advice>{_PRESCRIPTIVE}))\b", re.IGNORECASE)


def get_deps(config: RunnableConfig) -> GraphDeps:
    """Pulls the injected dependency bundle out of the run config."""
    deps = config["configurable"]["deps"]
    assert isinstance(deps, GraphDeps)
    return deps


def contains_advice(text: str) -> bool:
    """True when the text tells someone to trade, not when it describes flow."""
    return any(m.group("advice") for m in _ADVICE.finditer(text or ""))


def news_for_ticker(news_items: list[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    """News items that name the ticker, or all items when none are ticker-tagged."""
    tagged = [n for n in news_items if ticker in (n.get("tickers") or [])]
    return tagged if tagged else news_items


def newest(news_items: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """The newest cap items, so one busy news day cannot blow a provider limit."""
    ordered = sorted(
        news_items,
        key=lambda n: (str(n.get("published_at") or ""), str(n.get("item_id") or "")),
        reverse=True,
    )
    return ordered[:cap]


def item_ids(news_items: list[dict[str, Any]]) -> set[str]:
    """The set of supplied news item_ids, for the grounding check."""
    return {str(n["item_id"]) for n in news_items if n.get("item_id") is not None}


def user_payload(payload: dict[str, Any]) -> str:
    """Serializes a node's input context to compact JSON for the model."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def value_confidence(objective: str, value: dict[str, Any]) -> float:
    """The artifact-level confidence for an objective's value block."""
    kind = spec_for(objective).artifact_type
    if kind in ("SENTIMENT", "ARTICLE_SENTIMENT"):
        return float(value.get("self_confidence", 0.0))
    if kind == "INSIGHT":
        return float(value.get("confidence", 0.0))
    if kind == "CASH_DIVIDEND":
        return float(value.get("filing_confidence", 0.0))
    events = value.get("events") or []
    return min((float(e.get("confidence", 0.0)) for e in events), default=0.4)
