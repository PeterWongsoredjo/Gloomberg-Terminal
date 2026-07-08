"""End-to-end graph tests: the 04 5.1-5.6 fault paths, all offline and deterministic."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agentic.deps import GraphDeps
from app.agentic.graph import build_graph
from app.agentic.providers.base import ProviderRequest, ProviderUnavailable
from app.agentic.runner import run_agentic

from .conftest import RecordingTracer, ScriptedProvider, make_slot, sentiment_response


async def _run(deps: GraphDeps, universe: list[str], objective: str = "daily_sentiment") -> dict[str, Any]:
    """Compiles a fresh graph and runs it over a universe."""
    return await run_agentic(build_graph(None), deps, objective=objective, trade_date="2026-07-03", universe=universe)


def _slots(responder: Callable[[ProviderRequest], Any]) -> dict[str, Any]:
    """A single-provider slot map from one responder."""
    return {"groq": make_slot(ScriptedProvider("groq", responder))}


async def test_happy_path_produces_grounded_artifact(deps_factory: Callable[..., GraphDeps]) -> None:
    """A valid grounded response yields one SUCCEEDED CT-009 artifact."""
    deps = deps_factory(_slots(lambda _r: sentiment_response(label="BULLISH")))
    final = await _run(deps, ["TLKM"])
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 1
    assert final["artifacts"][0]["subject"]["ticker"] == "TLKM"


async def test_unknown_ticker_pruned_at_ingest(deps_factory: Callable[..., GraphDeps]) -> None:
    """A code not in dim_security is dropped before any analysis (04 5.4)."""
    deps = deps_factory(_slots(lambda _r: sentiment_response()))
    final = await _run(deps, ["TLKM", "ZZZZ"])
    assert final["subject_universe"] == ["TLKM"]


async def test_malformed_output_self_corrects(deps_factory: Callable[..., GraphDeps]) -> None:
    """An invalid first response self-corrects within the cap and lands (04 5.1)."""
    calls = {"n": 0}

    def responder(_req: ProviderRequest) -> Any:
        calls["n"] += 1
        return sentiment_response(invalid=calls["n"] == 1, label="BULLISH")

    final = await _run(deps_factory(_slots(responder)), ["TLKM"])
    assert final["status"] == "SUCCEEDED"
    assert len(final["artifacts"]) == 1


async def test_persistent_malformed_drops_and_degrades(deps_factory: Callable[..., GraphDeps]) -> None:
    """An always-invalid response is dropped at the cap; the run degrades (04 5.1/5.2)."""
    final = await _run(deps_factory(_slots(lambda _r: sentiment_response(invalid=True))), ["TLKM"])
    assert final["status"] == "DEGRADED"
    assert final["artifacts"] == []
    assert final["budget"]["consumed_iterations"] == 3


async def test_ungrounded_output_never_accepted(deps_factory: Callable[..., GraphDeps]) -> None:
    """Evidence not in the supplied items fails grounding and is dropped (04 5.5)."""
    responder = lambda _r: sentiment_response(evidence=["ghost:999"], label="BULLISH")  # noqa: E731
    final = await _run(deps_factory(_slots(responder)), ["TLKM"])
    assert final["status"] == "DEGRADED"
    assert final["artifacts"] == []


async def test_advisory_language_rejected(deps_factory: Callable[..., GraphDeps]) -> None:
    """Prescriptive language fails the non-advisory gate and is dropped (04 1.2/5.1)."""
    responder = lambda _r: sentiment_response(drivers=["buy this stock now"])  # noqa: E731
    final = await _run(deps_factory(_slots(responder)), ["TLKM"])
    assert final["status"] == "DEGRADED"
    assert final["artifacts"] == []


async def test_split_does_not_yield_false_bearish(deps_factory: Callable[..., GraphDeps]) -> None:
    """A bearish read on a corporate-action move fails context consistency (04 5.4)."""
    responder = lambda _r: sentiment_response(label="BEARISH", score=-0.7, drivers=["saham anjlok"])  # noqa: E731
    final = await _run(deps_factory(_slots(responder)), ["BBCA"])
    assert final["status"] == "DEGRADED"
    assert final["artifacts"] == []


async def test_dual_outage_degrades_without_fabricating(deps_factory: Callable[..., GraphDeps]) -> None:
    """Both providers down and no cache ends DEGRADED with no fabricated value (04 5.3)."""

    def dead(_req: ProviderRequest) -> Any:
        raise ProviderUnavailable("503")

    slots = {"groq": make_slot(ScriptedProvider("groq", dead)), "gemini": make_slot(ScriptedProvider("gemini", dead))}
    final = await _run(deps_factory(slots), ["TLKM"])
    assert final["status"] == "DEGRADED"
    assert final["artifacts"] == []
    assert "all providers down" in (final["abort_reason"] or "")


async def test_every_node_emits_a_span(deps_factory: Callable[..., GraphDeps]) -> None:
    """Each node opens a trace span under the run (04 5.6)."""
    tracer = RecordingTracer()
    deps = deps_factory(_slots(lambda _r: sentiment_response()), tracer=tracer)
    await _run(deps, ["TLKM"])
    for node in ("ingest_context", "cache_lookup", "route_task", "daily_sentiment", "evaluate", "finalize"):
        assert node in tracer.spans


async def test_budget_cap_is_hard(deps_factory: Callable[..., GraphDeps]) -> None:
    """A never-satisfied evaluator terminates at the iteration cap, no runaway (04 5.2)."""
    final = await _run(deps_factory(_slots(lambda _r: sentiment_response(evidence=["ghost:1"]))), ["TLKM"])
    assert final["budget"]["consumed_iterations"] == final["budget"]["max_loop_iterations"]
    assert final["status"] == "DEGRADED"
