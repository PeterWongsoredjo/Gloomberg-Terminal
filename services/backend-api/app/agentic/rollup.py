"""
Turns per-article sentiment into per-ticker sentiment, with no model call at all.

A ticker's score is a weighted mean of the articles that named it: how central the
ticker was to each article, how recent the article is, and how sure the model was.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

RELEVANCE_WEIGHT = {"PRIMARY": 1.0, "SECONDARY": 0.55, "INCIDENTAL": 0.20}

HALF_LIFE_HOURS = 6.0

NEUTRAL_BAND = 0.15

CONFIDENCE_SATURATION = 3.0

MAX_EVIDENCE_ITEMS = 10

PROMPT_VERSION = "rollup-v1"


def label_for(score: float) -> str:
    """The label a score earns, with a deadband so near-zero never reads directional."""
    if score >= NEUTRAL_BAND:
        return "BULLISH"
    if score <= -NEUTRAL_BAND:
        return "BEARISH"
    return "NEUTRAL"


def day_end(trade_date: date) -> datetime:
    """The anchor recency decays toward, so a rebuild of an old day is reproducible."""
    return datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=timezone.utc) + timedelta(days=1)


def _age_hours(published_at: Any, anchor: datetime) -> float:
    if not isinstance(published_at, datetime):
        return 0.0
    stamp = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    return max(0.0, (anchor - stamp).total_seconds() / 3600.0)


def _weight(row: dict[str, Any], anchor: datetime) -> float:
    relevance = RELEVANCE_WEIGHT.get(str(row.get("relevance")), 0.0)
    recency: float = 0.5 ** (_age_hours(row.get("published_at"), anchor) / HALF_LIFE_HOURS)
    return relevance * recency * float(row.get("confidence") or 0.0)


def _effective_sample(weights: list[float]) -> float:
    """Kish effective sample size, so many weak mentions do not fake a strong read."""
    total = sum(weights)
    squared = sum(w * w for w in weights)
    return (total * total) / squared if squared else 0.0


def _normalized(weights: list[float]) -> list[float]:
    """Rescales to a max of one; every use is a ratio, and tiny weights square to zero."""
    heaviest = max(weights, default=0.0)
    return [w / heaviest for w in weights] if heaviest > 0 else weights


def _ticker_row(ticker: str, weighted: list[tuple[float, dict[str, Any]]]) -> dict[str, Any] | None:
    """One ticker's rolled-up read, or None when nothing carried any weight."""
    contributing = [(w, r) for w, r in weighted if w > 0]
    if not contributing:
        return None

    scaled = _normalized([w for w, _ in contributing])
    total = sum(scaled)
    if total <= 0:
        return None

    score = sum(w * float(r["sentiment_score"]) for w, (_, r) in zip(scaled, contributing, strict=True)) / total
    confidence = sum(w * float(r["confidence"]) for w, (_, r) in zip(scaled, contributing, strict=True)) / total
    support = min(1.0, _effective_sample(scaled) / CONFIDENCE_SATURATION)
    ranked = sorted(zip(scaled, contributing, strict=True), key=lambda pair: pair[0], reverse=True)
    heaviest = ranked[0][1][1]
    evidence = [r["item_id"] for _, (_, r) in ranked]

    return {
        "ticker": ticker,
        "sentiment_score": round(score, 4),
        "sentiment_label": label_for(score),
        "confidence": round(confidence * support, 4),
        "evidence_item_ids": [str(i) for i in evidence[:MAX_EVIDENCE_ITEMS]],
        "artifact_id": str(heaviest["artifact_id"]),
        "provider": str(heaviest["provider"]),
        "model": str(heaviest["model"]),
    }


def roll_up(rows: list[dict[str, Any]], anchor: datetime) -> list[dict[str, Any]]:
    """Every ticker mentioned that day, scored from the articles that mentioned it."""
    grouped: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ticker"])].append((_weight(row, anchor), row))

    rolled = (_ticker_row(ticker, weighted) for ticker, weighted in sorted(grouped.items()))
    return [r for r in rolled if r is not None]


def upsert_tuples(
    rolled: list[dict[str, Any]], trade_date: date, run_id: str, trace_id: str | None, generated_at: datetime
) -> list[tuple[Any, ...]]:
    """Shapes rolled-up rows for the intraday.sentiment upsert."""
    return [
        (
            r["ticker"],
            trade_date,
            r["artifact_id"],
            run_id,
            trace_id,
            r["sentiment_score"],
            r["sentiment_label"],
            r["confidence"],
            json.dumps(["DERIVED_ESTIMATE"]),
            r["provider"],
            r["model"],
            PROMPT_VERSION,
            r["evidence_item_ids"],
            generated_at,
        )
        for r in rolled
    ]
