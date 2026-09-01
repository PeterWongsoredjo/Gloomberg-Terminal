"""The article-sentiment and insight paths: objective wiring, evaluator re-keying,
and the projection lifecycle over a live Postgres (skipped when the container is down)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import asyncpg
import pytest

from app.agentic import intraday
from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.nodes import ingest_context as ingest_context_mod
from app.agentic.nodes._common import contains_advice, newest, value_confidence
from app.agentic.nodes.evaluate import _advisory_text, _entities_resolved, _grounded
from app.agentic.objectives import spec_for, spec_for_type
from app.agentic.prompts.registry import get_prompt
from app.agentic.providers.base import ProviderResponse
from app.agentic.resolver import EntityResolver
from app.agentic.runner import run_agentic
from app.agentic.state import AgentState
from app.agentic.warehouse import GoldReader

from .conftest import ScriptedProvider, article_batch_response, article_verdict, make_slot


def insight_response(*, provider: str = "groq", confidence: float = 0.7) -> ProviderResponse:
    """Builds a normalized, schema-valid insight response."""
    parsed: dict[str, Any] = {
        "headline": "BBCA trades lower on split-adjusted volume",
        "narrative": "Descriptive read only: price action tracks the corporate action.",
        "signals": [{"type": "FLOW", "value": "net_foreign negative"}],
        "contradictions": ["news mildly positive vs foreign outflow"],
        "confidence": confidence,
    }
    return ProviderResponse(provider, "fake-model", "{}", parsed, 100, 20, 5, 200)


_TD = "2099-01-02"
_PREFIX = "testintr:"


def _news_item(item_id: str, tickers: list[str], title: str = "judul") -> dict[str, Any]:
    return {
        "item_id": item_id,
        "trade_date": "2026-07-03",
        "source": "cnbc_market",
        "lang": "id",
        "title": title,
        "summary": "ringkasan",
        "url": "http://x",
        "published_at": "2026-07-03T03:00:00+00:00",
        "tickers": tickers,
    }


def _patch_news(monkeypatch: pytest.MonkeyPatch, feed: list[dict[str, Any]], objective: str) -> None:
    """Stands in for the claim/day-items read so graph runs stay offline."""

    async def fake_news(
        deps: GraphDeps, gold: GoldReader, state: AgentState, obj: str, trade_date: str
    ) -> list[dict[str, Any]]:
        return feed if obj == objective else []

    monkeypatch.setattr(ingest_context_mod, "_news_for", fake_news)


# --- pure wiring ---


def test_article_sentiment_spec_reuses_the_sentiment_node() -> None:
    spec = spec_for("article_sentiment")
    assert spec.artifact_type == "ARTICLE_SENTIMENT"
    assert spec.node_name == "sentiment_analyze"
    assert spec.gate_attr == "sentiment_confidence_gate"
    assert spec.ladder == ("gemini", "groq")


def test_the_batch_shape_differs_from_the_stored_shape() -> None:
    """One request returns many verdicts, but each artifact stores exactly one."""
    spec = spec_for("article_sentiment")
    assert spec.response_model.__name__ == "ArticleSentimentBatch"
    assert spec.value_model.__name__ == "ArticleSentimentValue"


def test_retired_intraday_sentiment_objective_is_gone() -> None:
    """Two writers on intraday.sentiment with different meanings is what caused the bug."""
    with pytest.raises(KeyError):
        spec_for("intraday_sentiment")


def test_sentiment_type_stays_pinned_to_daily() -> None:
    assert spec_for_type("SENTIMENT").objective == "daily_sentiment"
    assert spec_for_type("ARTICLE_SENTIMENT").objective == "article_sentiment"


def test_article_prompt_is_registered() -> None:
    prompt = get_prompt("article_sentiment")
    assert prompt.version == "art-sent-v3"
    assert "NON-ADVISORY" in prompt.system_contract
    assert "IHSG" in prompt.system_contract
    assert "rationale" in prompt.system_contract


def test_the_article_prompt_covers_corporate_actions() -> None:
    """Corporate actions ride this prompt, and the entity gate drops a verdict with no issuer."""
    contract = get_prompt("article_sentiment").system_contract
    assert "CORPORATE ACTIONS" in contract
    assert "idx_corporate_action" in contract
    assert "ALWAYS PRIMARY" in contract


def test_value_confidence_rekeys_by_artifact_type() -> None:
    assert value_confidence("article_sentiment", {"self_confidence": 0.9}) == 0.9


def test_grounding_pins_a_verdict_to_its_own_batch() -> None:
    """A fabricated item_id fails that one article, never the whole batch."""
    good = {"value": {"item_id": "poll:1"}, "evidence_pool": ["poll:1"], "batch_pool": ["poll:1", "poll:2"]}
    bad = {"value": {"item_id": "invented"}, "evidence_pool": ["invented"], "batch_pool": ["poll:1", "poll:2"]}
    assert _grounded("article_sentiment", good) is True
    assert _grounded("article_sentiment", bad) is False


def test_a_ticker_less_neutral_article_still_passes() -> None:
    """Two thirds of the feed is macro with no issuer; that has to be a valid answer."""
    assert _entities_resolved("article_sentiment", {"ticker_sentiments": [], "sentiment_label": "NEUTRAL"}) is True


def test_a_ticker_less_directional_article_is_rejected() -> None:
    """A bullish read has to be bullish about somebody."""
    assert _entities_resolved("article_sentiment", {"ticker_sentiments": [], "sentiment_label": "BULLISH"}) is False


def test_advisory_gate_scans_article_drivers() -> None:
    assert _advisory_text("article_sentiment", {"drivers": ["laba naik", "cuan"]}) == "laba naik cuan"


def test_advisory_gate_scans_the_article_rationale() -> None:
    """The rationale is free model prose, so it is the likeliest place advice sneaks in."""
    value = {"drivers": ["laba naik"], "rationale": "Investor sebaiknya beli saham ini sekarang."}
    assert contains_advice(_advisory_text("article_sentiment", value)) is True


def test_advisory_gate_scans_insight_watchpoints() -> None:
    """Watchpoints name what is unresolved; a direction call there is still advice."""
    value = {
        "headline": "Flat close",
        "narrative": "Foreign flow ran against the news.",
        "watchpoints": ["Target price revision due 30 Jul"],
    }
    assert contains_advice(_advisory_text("insight_synthesis", value)) is True


def test_advisory_text_skips_the_fields_an_artifact_left_empty() -> None:
    """A missing rationale must not pad the scanned text with stray whitespace."""
    assert _advisory_text("article_sentiment", {"drivers": ["cuan"], "rationale": None}) == "cuan"
    assert _advisory_text("article_sentiment", {}) == ""


def test_intraday_insight_spec_reuses_the_insight_node() -> None:
    spec = spec_for("intraday_insight")
    assert spec.artifact_type == "INSIGHT"
    assert spec.node_name == "synthesize_insight"
    assert spec.gate_attr == "insight_confidence_gate"


def test_insight_type_stays_pinned_to_synthesis() -> None:
    assert spec_for_type("INSIGHT").objective == "insight_synthesis"


def test_intraday_insight_prompt_is_registered() -> None:
    prompt = get_prompt("intraday_insight")
    assert prompt.version == "ins-intraday-v3"
    assert "NON-ADVISORY" in prompt.system_contract


def test_the_eod_prompt_concludes_the_day_and_the_intraday_one_does_not() -> None:
    """Scope is read off the prompt version, so the two must stay distinguishable."""
    eod = get_prompt("insight_synthesis")
    intraday_prompt = get_prompt("intraday_insight")
    assert eod.version == "ins-eod-v1"
    assert "watchpoints" in eod.system_contract
    assert "NOT FORECASTS" in eod.system_contract
    assert "empty array" in intraday_prompt.system_contract


def test_newest_caps_to_the_most_recent_items() -> None:
    items = [{"item_id": f"i{n}", "published_at": f"2026-07-15T0{n}:00:00+00:00"} for n in range(5)]
    capped = newest(items, 2)
    assert [i["item_id"] for i in capped] == ["i4", "i3"]
    assert newest(items, 10) == sorted(items, key=lambda i: str(i["published_at"]), reverse=True)


def test_resolver_admits_index_subjects_only_explicitly() -> None:
    resolver = EntityResolver({"BBCA"})
    resolution = resolver.resolve(["IHSG", "BBCA", "LQ45", "GOTO"])
    assert resolution.resolved == ["IHSG", "BBCA"]
    assert resolution.unresolved == ["LQ45", "GOTO"]


async def test_index_context_shapes_like_a_market_row(gold_conn: Any) -> None:
    row = await GoldReader(gold_conn).index_context("IHSG", "2026-07-03")
    assert row is not None
    assert row["ticker"] == "IHSG" and row["security_id"] is None
    assert row["board"] == "INDEX" and row["is_fca"] is False
    assert row["close_level"] == 7350.2 and row["change_level"] == -41.3


async def test_index_context_missing_index_is_none(gold_conn: Any) -> None:
    assert await GoldReader(gold_conn).index_context("LQ45", "2026-07-03") is None




async def test_one_request_produces_one_artifact_per_article(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch fans out, so three articles in one call become three artifacts."""
    feed = [_news_item(f"poll:{n}", ["BBCA"]) for n in range(3)]
    _patch_news(monkeypatch, feed, "article_sentiment")
    calls: list[int] = []

    def responder(_request: Any) -> ProviderResponse:
        calls.append(1)
        return article_batch_response([article_verdict(i["item_id"]) for i in feed])

    slots = {"gemini": make_slot(ScriptedProvider("gemini", responder))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert len(calls) == 1  # one request
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 3  # three artifacts
    assert {a["value"]["item_id"] for a in final["artifacts"]} == {"poll:0", "poll:1", "poll:2"}
    assert all(a["artifact_type"] == "ARTICLE_SENTIMENT" for a in final["artifacts"])


async def test_articles_are_chunked_by_batch_size(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twenty-five articles at a batch size of ten is three requests, not twenty-five."""
    feed = [_news_item(f"poll:{n}", ["BBCA"]) for n in range(25)]
    _patch_news(monkeypatch, feed, "article_sentiment")
    seen: list[int] = []

    def responder(request: Any) -> ProviderResponse:
        import json

        articles = json.loads(request.user)["articles"]
        seen.append(len(articles))
        return article_batch_response([article_verdict(a["item_id"]) for a in articles])

    slots = {"gemini": make_slot(ScriptedProvider("gemini", responder))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert seen == [10, 10, 5]
    assert len(final["artifacts"]) == 25


async def test_a_fabricated_item_id_sinks_only_its_own_article(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad verdict must not cost the other articles in the same request."""
    feed = [_news_item(f"poll:{n}", ["BBCA"]) for n in range(3)]
    _patch_news(monkeypatch, feed, "article_sentiment")
    verdicts = [article_verdict("poll:0"), article_verdict("hallucinated"), article_verdict("poll:2")]

    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response(verdicts)))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert final["status"] == "SUCCEEDED"
    assert {a["value"]["item_id"] for a in final["artifacts"]} == {"poll:0", "poll:2"}


async def test_an_unlisted_ticker_is_dropped_not_stored(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model may invent a code; the registry decides, and the article still lands."""
    feed = [_news_item("poll:1", ["BBCA"])]
    _patch_news(monkeypatch, feed, "article_sentiment")
    verdict = article_verdict(
        "poll:1",
        tickers=[
            {"ticker": "BBCA", "sentiment_score": 0.3, "sentiment_label": "BULLISH", "relevance": "PRIMARY"},
            {"ticker": "UMKM", "sentiment_score": 0.1, "sentiment_label": "NEUTRAL", "relevance": "INCIDENTAL"},
        ],
    )

    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response([verdict])))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    artifact = final["artifacts"][0]
    assert [t["ticker"] for t in artifact["value"]["ticker_sentiments"]] == ["BBCA"]
    assert artifact["value"]["dropped_tickers"] == ["UMKM"]


async def test_a_delisted_issuer_named_by_the_item_survives_the_registry_gate(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delisting is about an issuer the registry no longer lists, so the gate must not eat it.

    Without this the whole DELISTING class burns three attempts and lands UNSCOREABLE:
    the resolver only knows currently listed issuers, and a delisting verdict is never NEUTRAL.
    """
    feed = [_news_item("corp_action:idx_ca:82392", ["MFIN"], title="MFIN delisting, effective 22 Jun 2026")]
    _patch_news(monkeypatch, feed, "article_sentiment")
    verdict = article_verdict(
        "corp_action:idx_ca:82392",
        label="BEARISH",
        score=-0.6,
        tickers=[
            {"ticker": "MFIN", "sentiment_score": -0.6, "sentiment_label": "BEARISH", "relevance": "PRIMARY"},
        ],
    )

    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response([verdict])))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert final["status"] == "SUCCEEDED"
    artifact = final["artifacts"][0]
    assert [t["ticker"] for t in artifact["value"]["ticker_sentiments"]] == ["MFIN"]
    assert artifact["value"]["dropped_tickers"] == []


async def test_an_invented_ticker_is_still_dropped_when_the_item_never_named_it(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusting the item's own tickers must not reopen the hallucination hole."""
    feed = [_news_item("poll:1", ["BBCA"])]
    _patch_news(monkeypatch, feed, "article_sentiment")
    verdict = article_verdict(
        "poll:1",
        tickers=[
            {"ticker": "BBCA", "sentiment_score": 0.3, "sentiment_label": "BULLISH", "relevance": "PRIMARY"},
            {"ticker": "ZZZZ", "sentiment_score": 0.1, "sentiment_label": "NEUTRAL", "relevance": "INCIDENTAL"},
        ],
    )

    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response([verdict])))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert final["artifacts"][0]["value"]["dropped_tickers"] == ["ZZZZ"]


async def test_a_macro_article_with_no_issuer_still_scores(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most of the live feed is macro; if this fails, the feed stays grey."""
    feed = [_news_item("poll:macro", [], title="Rupiah melemah usai Gubernur BI mundur")]
    _patch_news(monkeypatch, feed, "article_sentiment")
    verdict = article_verdict("poll:macro", label="NEUTRAL", score=0.0, tickers=[])

    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response([verdict])))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert final["status"] == "SUCCEEDED"
    assert final["artifacts"][0]["value"]["item_id"] == "poll:macro"


async def test_an_empty_claim_succeeds_rather_than_degrades(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing left to score is a finished day, not a failure to alert on."""
    _patch_news(monkeypatch, [], "article_sentiment")
    slots = {"gemini": make_slot(ScriptedProvider("gemini", lambda _r: article_batch_response([])))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="article_sentiment",
        trade_date="2026-07-03", universe=[],
    )

    assert final["status"] == "SUCCEEDED"
    assert final["artifacts"] == []


async def test_insight_runs_for_a_subject_gold_has_never_heard_of(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """IHSG has no dim_security row; before this it silently produced no insight at all."""
    _patch_news(monkeypatch, [_news_item("poll:9", ["IHSG"])], "intraday_insight")
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: insight_response()))}
    final = await run_agentic(
        build_graph(None), deps_factory(slots), objective="intraday_insight",
        trade_date="2026-07-03", universe=["IHSG"],
    )

    assert final["status"] == "SUCCEEDED"
    artifact = final["artifacts"][0]
    assert artifact["artifact_type"] == "INSIGHT"
    assert artifact["subject"]["ticker"] == "IHSG"


async def test_insight_survives_an_unbuilt_gold(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Market context is enrichment, not a gate; an empty Gold must not zero the subjects."""
    _patch_news(monkeypatch, [_news_item("poll:9", ["ZZZZ"])], "intraday_insight")
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: insight_response()))}
    deps = deps_factory(slots)
    final = await run_agentic(
        build_graph(None), deps, objective="intraday_insight",
        trade_date="2026-07-03", universe=["BBCA"],
    )

    assert final["status"] == "SUCCEEDED"
    assert final["artifacts"][0]["subject"]["ticker"] == "BBCA"


# --- live-gated projection lifecycle ---

_ITEM_DDL = """
create schema if not exists intraday;
create table if not exists intraday.news_item (
    item_id text primary key,
    trade_date date not null,
    source text not null,
    lang text,
    title text not null,
    summary text,
    url text not null,
    published_at timestamptz not null,
    tickers text[] not null default '{}',
    candidate_tickers text[] not null default '{}',
    tagged_at timestamptz,
    ingest_run_id text,
    first_seen_at timestamptz not null default now(),
    score_attempts int not null default 0,
    score_status text not null default 'PENDING',
    last_attempt_run_id text,
    scored_at timestamptz,
    scored_run_id text
);
"""


async def _pool_or_skip() -> asyncpg.Pool:
    try:
        pool = await asyncpg.create_pool(get_agentic_settings().postgres_dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    await pool.execute(_ITEM_DDL)
    await intraday.setup(pool)
    return pool


async def _purge(pool: asyncpg.Pool) -> None:
    await pool.execute("delete from intraday.article_sentiment where item_id like $1", f"{_PREFIX}%")
    await pool.execute("delete from intraday.news_item where item_id like $1", f"{_PREFIX}%")
    await pool.execute("delete from intraday.sentiment where run_id like $1", f"{_PREFIX}%")
    await pool.execute("delete from intraday.insight where run_id like $1", f"{_PREFIX}%")


async def _seed_item(pool: asyncpg.Pool, suffix: str, attempts: int = 0) -> str:
    item_id = f"{_PREFIX}{suffix}"
    await pool.execute(
        """
        insert into intraday.news_item
            (item_id, trade_date, source, title, url, published_at, tickers, score_attempts)
        values ($1, $2, 'cnbc_market', 'judul', 'http://x', now(), array['BBCA'], $3)
        on conflict (item_id) do nothing
        """,
        item_id,
        date.fromisoformat(_TD),
        attempts,
    )
    return item_id


def _state(run_id: str, item_ids: list[str], objective: str = "article_sentiment") -> AgentState:
    news = [{"item_id": i, "published_at": "2026-07-14T04:00:00+00:00"} for i in item_ids]
    return cast(
        AgentState,
        {
            "run_id": run_id,
            "objective": objective,
            "trade_date": _TD,
            "trace_id": "trace-1",
            "context": {"news_items": news, "market_context": [], "corporate_actions": []},
        },
    )


def _article_artifact(
    item_id: str,
    *,
    generated_at: str = "2026-07-14T04:00:00+00:00",
    artifact_id: str = f"{_PREFIX}art-1",
    tickers: list[dict[str, Any]] | None = None,
    rationale: str | None = "Foreign investors sold into the release.",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": "ARTICLE_SENTIMENT",
        "subject": {"security_id": None, "ticker": None},
        "value": {
            "item_id": item_id,
            "sentiment_score": -0.4,
            "sentiment_label": "BEARISH",
            "rationale": rationale,
            "drivers": ["asing jual"],
            "ticker_sentiments": tickers
            if tickers is not None
            else [
                {
                    "ticker": "BBCA",
                    "sentiment_score": -0.4,
                    "sentiment_label": "BEARISH",
                    "relevance": "PRIMARY",
                }
            ],
            "dropped_tickers": [],
            "self_confidence": 0.7,
        },
        "confidence": 0.7,
        "quality_flags": [],
        "provenance": {
            "provider": "gemini",
            "model": "fake-model",
            "prompt_version": "art-sent-v1",
            "generated_at": generated_at,
            "input_source_refs": [item_id],
        },
    }


async def test_claim_takes_the_oldest_pending_and_spends_its_attempt() -> None:
    pool = await _pool_or_skip()
    try:
        ids = [await _seed_item(pool, f"c{n}") for n in range(3)]
        claimed = await intraday.claim_unscored_batch(pool, _TD, 3, 2, f"{_PREFIX}run1")
        ours = [c for c in claimed if c["item_id"] in ids]
        assert len(ours) == 2
        rows = await pool.fetch(
            "select score_attempts from intraday.news_item where item_id = any($1)",
            [c["item_id"] for c in ours],
        )
        assert all(r["score_attempts"] == 1 for r in rows)
    finally:
        await _purge(pool)
        await pool.close()


async def test_overlapping_claims_never_take_the_same_item() -> None:
    """Two polls running at once must not both spend tokens on the same article."""
    pool = await _pool_or_skip()
    try:
        ids = {await _seed_item(pool, f"k{n}") for n in range(4)}
        async with pool.acquire() as first_conn, pool.acquire() as second_conn:
            async with first_conn.transaction(), second_conn.transaction():
                first = {
                    r["item_id"]
                    for r in await first_conn.fetch(
                        intraday._CLAIM_SQL, date.fromisoformat(_TD), 3, 2, "r1"
                    )
                } & ids
                second = {
                    r["item_id"]
                    for r in await second_conn.fetch(
                        intraday._CLAIM_SQL, date.fromisoformat(_TD), 3, 2, "r2"
                    )
                } & ids
        assert len(first) == 2 and len(second) == 2
        assert not (first & second)
    finally:
        await _purge(pool)
        await pool.close()


async def test_an_unsettled_item_is_reclaimed_by_the_next_poll() -> None:
    """A claim that never produced a verdict must come back around, not vanish."""
    pool = await _pool_or_skip()
    try:
        item_id = await _seed_item(pool, "retry1")
        first = await intraday.claim_unscored_batch(pool, _TD, 3, 10, "r1")
        assert item_id in {c["item_id"] for c in first}

        second = await intraday.claim_unscored_batch(pool, _TD, 3, 10, "r2")
        assert item_id in {c["item_id"] for c in second}

        row = await pool.fetchrow(
            "select score_attempts from intraday.news_item where item_id = $1", item_id
        )
        assert row is not None and row["score_attempts"] == 2
    finally:
        await _purge(pool)
        await pool.close()


async def test_a_final_attempt_marks_the_item_unscoreable() -> None:
    """A poison article stops burning quota instead of being retried forever."""
    pool = await _pool_or_skip()
    try:
        item_id = await _seed_item(pool, "x1", attempts=2)
        await intraday.claim_unscored_batch(pool, _TD, 3, 10, "r1")
        row = await pool.fetchrow(
            "select score_status from intraday.news_item where item_id = $1", item_id
        )
        assert row is not None and row["score_status"] == "UNSCOREABLE"
        again = await intraday.claim_unscored_batch(pool, _TD, 3, 10, "r2")
        assert item_id not in {c["item_id"] for c in again}
    finally:
        await _purge(pool)
        await pool.close()


async def test_settling_promotes_a_claimed_item_to_scored() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run2"
    try:
        item_id = await _seed_item(pool, "s1")
        await intraday.claim_unscored_batch(pool, _TD, 3, 10, run_id)
        await intraday.project_article_sentiment(
            pool, _state(run_id, [item_id]), [_article_artifact(item_id)]
        )
        row = await pool.fetchrow(
            "select score_status, scored_at from intraday.news_item where item_id = $1", item_id
        )
        assert row is not None and row["score_status"] == "SCORED" and row["scored_at"] is not None
    finally:
        await _purge(pool)
        await pool.close()


async def test_an_omitted_article_stays_pending_for_the_next_poll() -> None:
    """The model skipping one article costs it an attempt, not its whole chance."""
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run3"
    try:
        scored_id = await _seed_item(pool, "o1")
        skipped_id = await _seed_item(pool, "o2")
        await intraday.claim_unscored_batch(pool, _TD, 3, 10, run_id)
        await intraday.project_article_sentiment(
            pool, _state(run_id, [scored_id, skipped_id]), [_article_artifact(scored_id)]
        )
        row = await pool.fetchrow(
            "select score_status, score_attempts from intraday.news_item where item_id = $1", skipped_id
        )
        assert row is not None and row["score_status"] == "PENDING" and row["score_attempts"] == 1
    finally:
        await _purge(pool)
        await pool.close()


async def test_article_projection_lands_both_grains() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run4"
    try:
        item_id = await _seed_item(pool, "g1")
        await intraday.project_article_sentiment(
            pool,
            _state(run_id, [item_id]),
            [
                _article_artifact(
                    item_id,
                    tickers=[
                        {"ticker": "BBCA", "sentiment_score": -0.4, "sentiment_label": "BEARISH", "relevance": "PRIMARY"},
                        {"ticker": "BBRI", "sentiment_score": 0.2, "sentiment_label": "BULLISH", "relevance": "SECONDARY"},
                    ],
                )
            ],
        )
        article = await pool.fetchrow(
            "select * from intraday.article_sentiment where item_id = $1", item_id
        )
        assert article is not None and article["sentiment_label"] == "BEARISH"

        per_ticker = await pool.fetch(
            "select ticker, relevance from intraday.article_ticker_sentiment "
            "where item_id = $1 order by ticker",
            item_id,
        )
        assert [(r["ticker"], r["relevance"]) for r in per_ticker] == [
            ("BBCA", "PRIMARY"),
            ("BBRI", "SECONDARY"),
        ]
    finally:
        await _purge(pool)
        await pool.close()


async def test_rollup_source_feeds_the_deterministic_rollup() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run5"
    try:
        item_id = await _seed_item(pool, "r1")
        await intraday.project_article_sentiment(
            pool, _state(run_id, [item_id]), [_article_artifact(item_id)]
        )
        rows = await intraday.rollup_source(pool, _TD)
        ours = [r for r in rows if r["item_id"] == item_id]
        assert len(ours) == 1
        assert ours[0]["ticker"] == "BBCA" and ours[0]["relevance"] == "PRIMARY"
    finally:
        await _purge(pool)
        await pool.close()


async def test_insight_subjects_rank_by_article_count_and_fingerprint() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run6"
    try:
        ids = [await _seed_item(pool, f"i{n}") for n in range(2)]
        for n, item_id in enumerate(ids):
            await intraday.project_article_sentiment(
                pool,
                _state(run_id, [item_id]),
                [_article_artifact(item_id, artifact_id=f"{_PREFIX}a{n}")],
            )
        subjects = {s["ticker"]: s for s in await intraday.insight_subjects(pool, _TD)}
        assert subjects["BBCA"]["article_count"] >= 2
        assert subjects["BBCA"]["evidence_fingerprint"]
    finally:
        await _purge(pool)
        await pool.close()


# --- live-gated insight projection ---


def _insight_state(run_id: str) -> AgentState:
    return cast(
        AgentState,
        {
            "run_id": run_id,
            "objective": "intraday_insight",
            "trade_date": _TD,
            "trace_id": "trace-2",
            "context": {"news_items": [], "market_context": [], "corporate_actions": []},
        },
    )


def _insight_artifact(
    generated_at: str = "2026-07-14T04:00:00+00:00",
    artifact_id: str = f"{_PREFIX}ins-1",
    watchpoints: list[str] | None = None,
    prompt_version: str = "ins-intraday-v3",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": "INSIGHT",
        "subject": {"security_id": 1001, "ticker": "BBCA"},
        "value": {
            "headline": "BBCA steady on heavy foreign selling",
            "narrative": "Observations only.",
            "signals": [{"type": "FLOW", "value": "net_foreign negative"}],
            "contradictions": ["positive news vs outflow"],
            "watchpoints": watchpoints if watchpoints is not None else [],
            "confidence": 0.7,
        },
        "confidence": 0.7,
        "quality_flags": [],
        "provenance": {
            "provider": "groq",
            "model": "fake-model",
            "prompt_version": prompt_version,
            "generated_at": generated_at,
            "input_source_refs": [],
        },
    }


async def test_day_items_returns_scored_and_unscored_alike() -> None:
    pool = await _pool_or_skip()
    try:
        fresh = await _seed_item(pool, "day1")
        exhausted = await _seed_item(pool, "day2", attempts=3)
        rows = await intraday.day_items(pool, _TD)
        ours = {r["item_id"] for r in rows if str(r["item_id"]).startswith(_PREFIX)}
        assert {fresh, exhausted} <= ours
    finally:
        await _purge(pool)
        await pool.close()


async def test_project_insight_lands_row_without_touching_items() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}insrun1"
    try:
        item_id = await _seed_item(pool, "ins1")
        await intraday.project_insight(pool, _insight_state(run_id), [_insight_artifact()])

        row = await pool.fetchrow(
            "select * from intraday.insight where run_id = $1 and ticker = 'BBCA'", run_id
        )
        assert row is not None
        assert row["trace_id"] == "trace-2"
        assert row["headline"].startswith("BBCA steady")

        item = await pool.fetchrow(
            "select scored_at, score_attempts from intraday.news_item where item_id = $1", item_id
        )
        assert item is not None
        assert item["scored_at"] is None and item["score_attempts"] == 0
    finally:
        await _purge(pool)
        await pool.close()


def _placeholder_count(sql: str) -> int:
    return len({int(m) for m in re.findall(r"\$(\d+)", sql)})


def test_every_upsert_binds_exactly_as_many_values_as_it_declares() -> None:
    """Adding a column to one side and not the other only fails against a live database."""
    state = _state("run-x", ["i1"])
    article = intraday._article_row(
        _article_artifact("i1"), state, datetime(2026, 7, 14, tzinfo=UTC), "batch-1"
    )
    assert len(article) == _placeholder_count(intraday._ARTICLE_UPSERT_SQL)

    insight = intraday._insight_row(_insight_artifact(), _insight_state("run-y"))
    assert insight is not None
    assert len(insight) == _placeholder_count(intraday._INSIGHT_UPSERT_SQL)


def test_the_row_builders_carry_the_new_columns() -> None:
    """The rationale and the watchpoints have to actually reach the bound tuple."""
    state = _state("run-x", ["i1"])
    article = intraday._article_row(
        _article_artifact("i1"), state, datetime(2026, 7, 14, tzinfo=UTC), "batch-1"
    )
    assert "Foreign investors sold into the release." in article

    silent = intraday._article_row(
        _article_artifact("i1", rationale=None), state, datetime(2026, 7, 14, tzinfo=UTC), "b"
    )
    assert None in silent

    insight = intraday._insight_row(
        _insight_artifact(watchpoints=["RUPS 30 Jul"]), _insight_state("run-y")
    )
    assert insight is not None and '["RUPS 30 Jul"]' in insight


async def test_article_rationale_round_trips_and_absence_stays_absent() -> None:
    """A pre-v2 verdict has no rationale; storing an empty string instead would be a lie."""
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}ratrun"
    try:
        explained = await _seed_item(pool, "rat1")
        silent = await _seed_item(pool, "rat2")
        await intraday.project_article_sentiment(
            pool,
            _state(run_id, [explained, silent]),
            [
                _article_artifact(explained, artifact_id=f"{_PREFIX}rat-a"),
                _article_artifact(silent, artifact_id=f"{_PREFIX}rat-b", rationale=None),
            ],
        )
        rows = {
            r["item_id"]: r["rationale"]
            for r in await pool.fetch(
                "select item_id, rationale from intraday.article_sentiment where item_id = any($1)",
                [explained, silent],
            )
        }
        assert rows[explained] == "Foreign investors sold into the release."
        assert rows[silent] is None
    finally:
        await _purge(pool)
        await pool.close()


async def test_insight_watchpoints_round_trip() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}wprun"
    try:
        carried = ["RUPS scheduled 30 Jul", "Foreign flow negative 4 sessions"]
        await intraday.project_insight(
            pool, _insight_state(run_id), [_insight_artifact(watchpoints=carried)]
        )
        row = await pool.fetchrow(
            "select watchpoints from intraday.insight where run_id = $1 and ticker = 'BBCA'", run_id
        )
        assert row is not None and json.loads(row["watchpoints"]) == carried
    finally:
        await _purge(pool)
        await pool.close()


async def test_the_close_of_day_read_supersedes_the_last_hourly_one() -> None:
    """Both land on (ticker, trade_date); the 17:00 conclusion has to win the day."""
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}eodrun"
    try:
        hourly = _insight_artifact(
            generated_at="2026-07-14T08:00:00+00:00", artifact_id=f"{_PREFIX}ins-hourly"
        )
        eod = _insight_artifact(
            generated_at="2026-07-14T10:00:00+00:00",
            artifact_id=f"{_PREFIX}ins-eod",
            watchpoints=["Dividend not yet declared"],
            prompt_version="ins-eod-v1",
        )
        await intraday.project_insight(pool, _insight_state(run_id), [hourly])
        await intraday.project_insight(pool, _insight_state(run_id), [eod])

        rows = await pool.fetch(
            "select artifact_id, prompt_version, watchpoints from intraday.insight "
            "where run_id = $1 and ticker = 'BBCA'",
            run_id,
        )
        assert len(rows) == 1
        assert rows[0]["artifact_id"] == eod["artifact_id"]
        assert rows[0]["prompt_version"] == "ins-eod-v1"
        assert json.loads(rows[0]["watchpoints"]) == ["Dividend not yet declared"]
    finally:
        await _purge(pool)
        await pool.close()


async def test_project_insight_upsert_is_freshest_wins() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}insrun2"
    try:
        newer = _insight_artifact(generated_at="2026-07-14T06:00:00+00:00", artifact_id=f"{_PREFIX}ins-new")
        older = _insight_artifact(generated_at="2026-07-14T05:00:00+00:00", artifact_id=f"{_PREFIX}ins-old")
        await intraday.project_insight(pool, _insight_state(run_id), [newer])
        await intraday.project_insight(pool, _insight_state(run_id), [older])
        row = await pool.fetchrow(
            "select artifact_id from intraday.insight where run_id = $1 and ticker = 'BBCA'", run_id
        )
        assert row is not None and row["artifact_id"] == newer["artifact_id"]
    finally:
        await _purge(pool)
        await pool.close()


# the regression: describing foreign flow rejected every insight that mentioned it
def test_net_flow_wording_is_description_not_advice() -> None:
    """Net buy and net sell are how IDX states foreign flow; they recommend nothing."""
    for text in (
        "Net buy asing hampir Rp1 triliun",
        "net sell asing melebar",
        "beli bersih investor asing",
        "jual bersih asing",
    ):
        assert contains_advice(text) is False, text


def test_a_flow_signal_no_longer_sinks_the_whole_insight() -> None:
    """The exact shape the model returns for BBRI, which the gate used to reject."""
    value = {
        "headline": "BBRI menguat ditopang aliran dana asing",
        "narrative": "BBRI naik pada perdagangan sesi pertama.",
        "signals": [{"type": "FLOW", "value": "Net buy asing hampir Rp1 triliun"}],
        "watchpoints": [],
    }
    assert contains_advice(_advisory_text("intraday_insight", value)) is False


def test_real_advice_still_fails_the_gate() -> None:
    """Loosening the flow wording must not let a genuine call through."""
    for text in ("Buy BBRI now", "sell into strength", "rekomendasi beli", "target price 6000"):
        assert contains_advice(text) is True, text
