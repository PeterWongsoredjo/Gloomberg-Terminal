"""The intraday sentiment and insight paths: objective wiring, evaluator re-keying,
and the projection lifecycle over a live Postgres (skipped when the container is down)."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import asyncpg
import pytest

from collections.abc import Callable

from app.agentic import intraday
from app.agentic.config import get_agentic_settings
from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.nodes import ingest_context as ingest_context_mod
from app.agentic.nodes._common import newest, value_confidence
from app.agentic.nodes.evaluate import _advisory_text, _grounded
from app.agentic.objectives import spec_for, spec_for_type
from app.agentic.prompts.registry import get_prompt
from app.agentic.resolver import EntityResolver
from app.agentic.runner import run_agentic
from app.agentic.state import AgentState
from app.agentic.warehouse import GoldReader

from app.agentic.providers.base import ProviderResponse

from .conftest import ScriptedProvider, make_slot, sentiment_response


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

_TD = "2026-07-14"
_PREFIX = "testintr:"


# --- pure wiring ---


def test_intraday_spec_reuses_the_sentiment_node() -> None:
    spec = spec_for("intraday_sentiment")
    assert spec.artifact_type == "SENTIMENT"
    assert spec.node_name == "sentiment_analyze"
    assert spec.gate_attr == "sentiment_confidence_gate"
    assert spec.ladder == ("groq", "gemini")


def test_sentiment_type_stays_pinned_to_daily() -> None:
    assert spec_for_type("SENTIMENT").objective == "daily_sentiment"


def test_intraday_prompt_is_registered() -> None:
    prompt = get_prompt("intraday_sentiment")
    assert prompt.version == "sent-intraday-v2"
    assert "NON-ADVISORY" in prompt.system_contract


def test_value_confidence_rekeys_by_artifact_type() -> None:
    assert value_confidence("intraday_sentiment", {"self_confidence": 0.9}) == 0.9


def test_grounding_enforced_for_intraday_drafts() -> None:
    draft = {"value": {"evidence_item_ids": ["outside"]}, "evidence_pool": ["inside"]}
    assert _grounded("intraday_sentiment", draft) is False
    draft_ok = {"value": {"evidence_item_ids": ["inside"]}, "evidence_pool": ["inside"]}
    assert _grounded("intraday_sentiment", draft_ok) is True


def test_advisory_gate_scans_intraday_drivers() -> None:
    assert _advisory_text("intraday_sentiment", {"drivers": ["laba naik", "cuan"]}) == "laba naik cuan"


def test_intraday_insight_spec_reuses_the_insight_node() -> None:
    spec = spec_for("intraday_insight")
    assert spec.artifact_type == "INSIGHT"
    assert spec.node_name == "synthesize_insight"
    assert spec.gate_attr == "insight_confidence_gate"
    assert spec.ladder == ("groq", "gemini")


def test_insight_type_stays_pinned_to_synthesis() -> None:
    assert spec_for_type("INSIGHT").objective == "insight_synthesis"


def test_intraday_insight_prompt_is_registered() -> None:
    prompt = get_prompt("intraday_insight")
    assert prompt.version == "ins-intraday-v2"
    assert "NON-ADVISORY" in prompt.system_contract


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


# --- offline graph run over a fake backlog ---


async def test_intraday_run_scores_the_supplied_backlog(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The intraday objective routes through the sentiment node over polled items."""
    backlog = [
        {
            "item_id": "poll:1",
            "trade_date": "2026-07-03",
            "source": "cnbc_market",
            "lang": "id",
            "title": "BBCA cetak laba",
            "summary": "laba naik",
            "url": "http://x",
            "published_at": "2026-07-03T03:00:00+00:00",
            "tickers": ["BBCA"],
        }
    ]

    async def fake_news(
        deps: GraphDeps, gold: GoldReader, objective: str, trade_date: str
    ) -> list[dict[str, Any]]:
        return backlog if objective == "intraday_sentiment" else []

    monkeypatch.setattr(ingest_context_mod, "_news_for", fake_news)
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: sentiment_response(evidence=["poll:1"])))}
    deps = deps_factory(slots)
    final = await run_agentic(
        build_graph(None), deps, objective="intraday_sentiment", trade_date="2026-07-03", universe=["BBCA"]
    )
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 1
    artifact = final["artifacts"][0]
    assert artifact["subject"]["ticker"] == "BBCA"
    assert artifact["value"]["evidence_item_ids"] == ["poll:1"]
    assert artifact["provenance"]["prompt_version"] == "sent-intraday-v2"


async def test_intraday_insight_run_synthesizes_over_the_day_feed(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hourly objective routes through the insight node over today's polled items."""
    feed = [
        {
            "item_id": "poll:9",
            "trade_date": "2026-07-03",
            "source": "kontan_investasi",
            "lang": "id",
            "title": "BBCA ramai dibahas",
            "summary": "volume naik",
            "url": "http://z",
            "published_at": "2026-07-03T04:00:00+00:00",
            "tickers": ["BBCA"],
        }
    ]

    async def fake_news(
        deps: GraphDeps, gold: GoldReader, objective: str, trade_date: str
    ) -> list[dict[str, Any]]:
        return feed if objective == "intraday_insight" else []

    monkeypatch.setattr(ingest_context_mod, "_news_for", fake_news)
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: insight_response()))}
    deps = deps_factory(slots)
    final = await run_agentic(
        build_graph(None), deps, objective="intraday_insight", trade_date="2026-07-03", universe=["BBCA"]
    )
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 1
    artifact = final["artifacts"][0]
    assert artifact["artifact_type"] == "INSIGHT"
    assert artifact["subject"]["ticker"] == "BBCA"
    assert artifact["value"]["contradictions"] == ["news mildly positive vs foreign outflow"]
    assert artifact["provenance"]["prompt_version"] == "ins-intraday-v2"


async def test_ihsg_scores_as_an_index_subject(
    deps_factory: Callable[..., GraphDeps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A market-wrap item tagged IHSG produces an index artifact with no security_id."""
    wrap = [
        {
            "item_id": "poll:wrap",
            "trade_date": "2026-07-03",
            "source": "antara_ekonomi",
            "lang": "id",
            "title": "IHSG menguat di sesi pertama",
            "summary": "sentimen pasar membaik",
            "url": "http://w",
            "published_at": "2026-07-03T03:30:00+00:00",
            "tickers": ["IHSG"],
        }
    ]

    async def fake_news(
        deps: GraphDeps, gold: GoldReader, objective: str, trade_date: str
    ) -> list[dict[str, Any]]:
        return wrap if objective == "intraday_sentiment" else []

    monkeypatch.setattr(ingest_context_mod, "_news_for", fake_news)
    slots = {"groq": make_slot(ScriptedProvider("groq", lambda _r: sentiment_response(evidence=["poll:wrap"])))}
    deps = deps_factory(slots)
    final = await run_agentic(
        build_graph(None), deps, objective="intraday_sentiment", trade_date="2026-07-03", universe=["IHSG"]
    )
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 1
    artifact = final["artifacts"][0]
    assert artifact["subject"]["ticker"] == "IHSG"
    assert artifact["subject"]["security_id"] is None
    assert artifact["value"]["evidence_item_ids"] == ["poll:wrap"]


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
    ingest_run_id text,
    first_seen_at timestamptz not null default now(),
    score_attempts int not null default 0,
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


def _state(run_id: str, item_ids: list[str]) -> AgentState:
    news = [{"item_id": i} for i in item_ids]
    return cast(
        AgentState,
        {
            "run_id": run_id,
            "objective": "intraday_sentiment",
            "trade_date": _TD,
            "trace_id": "trace-1",
            "context": {"news_items": news, "market_context": [], "corporate_actions": []},
        },
    )


def _artifact(
    item_ids: list[str],
    generated_at: str = "2026-07-14T04:00:00+00:00",
    artifact_id: str = f"{_PREFIX}art-1",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": "SENTIMENT",
        "subject": {"security_id": 1001, "ticker": "BBCA"},
        "value": {
            "sentiment_score": -0.4,
            "sentiment_label": "BEARISH",
            "drivers": ["asing jual"],
            "evidence_item_ids": item_ids[:1],
            "self_confidence": 0.7,
        },
        "confidence": 0.7,
        "quality_flags": [],
        "provenance": {
            "provider": "groq",
            "model": "fake-model",
            "prompt_version": "sent-intraday-v2",
            "generated_at": generated_at,
            "input_source_refs": item_ids,
        },
    }


async def test_unscored_items_returns_seeded_rows() -> None:
    pool = await _pool_or_skip()
    try:
        item_id = await _seed_item(pool, "u1")
        rows = await intraday.unscored_items(pool, _TD, max_attempts=3)
        ours = [r for r in rows if r["item_id"] == item_id]
        assert len(ours) == 1
        assert ours[0]["tickers"] == ["BBCA"]
    finally:
        await _purge(pool)
        await pool.close()


async def test_project_scores_items_and_lands_sentiment() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run1"
    try:
        ids = [await _seed_item(pool, "s1"), await _seed_item(pool, "s2")]
        await intraday.project(pool, _state(run_id, ids), [_artifact(ids)])

        row = await pool.fetchrow(
            "select * from intraday.sentiment where run_id = $1 and ticker = 'BBCA'", run_id
        )
        assert row is not None
        assert row["trace_id"] == "trace-1"
        assert row["sentiment_label"] == "BEARISH"
        assert list(row["evidence_item_ids"]) == ids[:1]

        items = await pool.fetch(
            "select scored_at, score_attempts from intraday.news_item where item_id = any($1)", ids
        )
        assert all(i["scored_at"] is not None and i["score_attempts"] == 1 for i in items)
        remaining = await intraday.unscored_items(pool, _TD, max_attempts=3)
        assert not [r for r in remaining if r["item_id"] in ids]
    finally:
        await _purge(pool)
        await pool.close()


async def test_degraded_run_bumps_attempts_without_scoring() -> None:
    pool = await _pool_or_skip()
    try:
        item_id = await _seed_item(pool, "d1")
        await intraday.project(pool, _state(f"{_PREFIX}run2", [item_id]), [])
        row = await pool.fetchrow(
            "select scored_at, score_attempts, last_attempt_run_id "
            "from intraday.news_item where item_id = $1",
            item_id,
        )
        assert row is not None
        assert row["scored_at"] is None and row["score_attempts"] == 1
        assert row["last_attempt_run_id"] == f"{_PREFIX}run2"
    finally:
        await _purge(pool)
        await pool.close()


async def test_exhausted_attempts_drop_out_of_the_backlog() -> None:
    pool = await _pool_or_skip()
    try:
        item_id = await _seed_item(pool, "x1", attempts=3)
        rows = await intraday.unscored_items(pool, _TD, max_attempts=3)
        assert not [r for r in rows if r["item_id"] == item_id]
    finally:
        await _purge(pool)
        await pool.close()


async def test_projection_upsert_is_freshest_wins() -> None:
    pool = await _pool_or_skip()
    run_id = f"{_PREFIX}run3"
    try:
        ids = [await _seed_item(pool, "f1")]
        newer = _artifact(ids, generated_at="2026-07-14T06:00:00+00:00", artifact_id=f"{_PREFIX}new")
        older = _artifact(ids, generated_at="2026-07-14T05:00:00+00:00", artifact_id=f"{_PREFIX}old")
        await intraday.project(pool, _state(run_id, ids), [newer])
        await intraday.project(pool, _state(run_id, ids), [older])
        row = await pool.fetchrow(
            "select artifact_id from intraday.sentiment where run_id = $1 and ticker = 'BBCA'", run_id
        )
        assert row is not None and row["artifact_id"] == newer["artifact_id"]
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
    generated_at: str = "2026-07-14T04:00:00+00:00", artifact_id: str = f"{_PREFIX}ins-1"
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
            "confidence": 0.7,
        },
        "confidence": 0.7,
        "quality_flags": [],
        "provenance": {
            "provider": "groq",
            "model": "fake-model",
            "prompt_version": "ins-intraday-v2",
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
