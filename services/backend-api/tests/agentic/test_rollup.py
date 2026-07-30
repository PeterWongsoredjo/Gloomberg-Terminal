"""The deterministic per-ticker rollup: weighting, the neutral band, and failing closed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.agentic import rollup

_ANCHOR = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)


def _row(
    ticker: str = "BBCA",
    score: float = 0.8,
    relevance: str = "PRIMARY",
    confidence: float = 1.0,
    age_hours: float = 0.0,
    item_id: str = "poll:1",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "sentiment_score": score,
        "relevance": relevance,
        "confidence": confidence,
        "published_at": _ANCHOR - timedelta(hours=age_hours),
        "item_id": item_id,
        "artifact_id": f"art-{item_id}",
        "provider": "gemini",
        "model": "fake-model",
        "prompt_version": "art-sent-v1",
    }


def test_a_single_primary_article_carries_straight_through() -> None:
    rolled = rollup.roll_up([_row(score=0.8)], _ANCHOR)
    assert len(rolled) == 1
    assert rolled[0]["ticker"] == "BBCA"
    assert rolled[0]["sentiment_score"] == 0.8
    assert rolled[0]["sentiment_label"] == "BULLISH"


def test_an_older_article_pulls_less_than_a_fresh_one() -> None:
    """Two half-lives back is a quarter of the weight, so the fresh read dominates."""
    rolled = rollup.roll_up([_row(score=1.0, age_hours=12, item_id="old"),
                             _row(score=0.0, age_hours=0, item_id="new")], _ANCHOR)
    assert rolled[0]["sentiment_score"] == 0.2


def test_a_passing_mention_cannot_outvote_the_main_subject() -> None:
    """An incidental bearish mention must not flip a primary bullish read."""
    rolled = rollup.roll_up(
        [_row(score=0.8, relevance="PRIMARY", item_id="a"),
         _row(score=-0.9, relevance="INCIDENTAL", item_id="b")],
        _ANCHOR,
    )
    assert rolled[0]["sentiment_score"] > 0
    assert rolled[0]["sentiment_label"] == "BULLISH"


def test_no_evidence_writes_no_row_rather_than_a_zero() -> None:
    """Silence is not neutrality; a zero here would render as a real reading."""
    weightless = _row(confidence=0.0)
    assert rollup.roll_up([weightless], _ANCHOR) == []

    unknown_relevance = _row(relevance="NOT_A_LEVEL")
    assert rollup.roll_up([unknown_relevance], _ANCHOR) == []


def test_the_neutral_band_stops_a_whisper_reading_as_a_signal() -> None:
    """This is what kept a 0.00 score rendering bright green in the feed."""
    assert rollup.label_for(0.10) == "NEUTRAL"
    assert rollup.label_for(0.0) == "NEUTRAL"
    assert rollup.label_for(-0.10) == "NEUTRAL"
    assert rollup.label_for(0.16) == "BULLISH"
    assert rollup.label_for(-0.16) == "BEARISH"


def test_one_thin_mention_scores_low_confidence() -> None:
    """A lone incidental mention should trip the low-confidence gate, not look certain."""
    rolled = rollup.roll_up([_row(relevance="INCIDENTAL", confidence=0.9)], _ANCHOR)
    assert rolled[0]["confidence"] < 0.5


def test_confidence_grows_with_corroboration() -> None:
    many = [_row(item_id=f"i{n}", confidence=0.9) for n in range(4)]
    one = [_row(item_id="i0", confidence=0.9)]
    assert rollup.roll_up(many, _ANCHOR)[0]["confidence"] > rollup.roll_up(one, _ANCHOR)[0]["confidence"]


def test_tickers_are_rolled_up_independently() -> None:
    rolled = {r["ticker"]: r for r in rollup.roll_up(
        [_row(ticker="BBCA", score=0.8, item_id="a"), _row(ticker="ANTM", score=-0.7, item_id="b")], _ANCHOR
    )}
    assert rolled["BBCA"]["sentiment_label"] == "BULLISH"
    assert rolled["ANTM"]["sentiment_label"] == "BEARISH"


def test_the_index_rolls_up_like_any_other_ticker() -> None:
    """IHSG needs no special case; this is what finally gives it a score."""
    rolled = rollup.roll_up([_row(ticker="IHSG", score=-0.5)], _ANCHOR)
    assert rolled[0]["ticker"] == "IHSG" and rolled[0]["sentiment_label"] == "BEARISH"


def test_evidence_cites_the_heaviest_articles_first() -> None:
    rows = [_row(item_id="light", relevance="INCIDENTAL"), _row(item_id="heavy", relevance="PRIMARY")]
    rolled = rollup.roll_up(rows, _ANCHOR)
    assert rolled[0]["evidence_item_ids"][0] == "heavy"
    assert rolled[0]["artifact_id"] == "art-heavy"


def test_upsert_tuples_flag_the_row_as_derived() -> None:
    """The score is computed from artifacts, not read from one, and says so."""
    rolled = rollup.roll_up([_row()], _ANCHOR)
    tuples = rollup.upsert_tuples(rolled, _ANCHOR.date(), "run-1", "trace-1", _ANCHOR)
    assert len(tuples) == 1
    assert tuples[0][0] == "BBCA"
    assert "DERIVED_ESTIMATE" in tuples[0][8]
    assert tuples[0][11] == rollup.PROMPT_VERSION


def test_confidence_survives_a_very_old_article() -> None:
    stale = _row(age_hours=5000, confidence=0.9)
    rolled = rollup.roll_up([stale], _ANCHOR)
    assert len(rolled) == 1
    assert rolled[0]["confidence"] > 0.0
    assert rolled[0]["sentiment_score"] == 0.8


def test_recency_is_anchored_to_the_day_not_to_now() -> None:
    rows = [_row(item_id="a", score=1.0, age_hours=0), _row(item_id="b", score=0.0, age_hours=12)]
    assert rollup.roll_up(rows, _ANCHOR)[0]["sentiment_score"] == 0.8


def test_day_end_anchors_to_midnight_after_the_trade_date() -> None:
    assert rollup.day_end(date(2026, 7, 27)) == datetime(2026, 7, 28, tzinfo=UTC)
