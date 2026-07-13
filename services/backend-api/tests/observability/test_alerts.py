"""OB-06 alerts: dedup collapses a storm, cooldown holds repeats, routing reaches sinks."""

from __future__ import annotations

from app.observability.alerts.engine import AlertEngine
from app.observability.slo.engine import Breach


def _coverage_breach() -> Breach:
    return Breach("coverage_floor", "ERROR", "coverage_ratio", 0.6, 0.95, dataset="idx_summary.daily_trade")


def test_coverage_gap_raises_one_alert_per_date_dataset() -> None:
    clock = [1000.0]
    engine = AlertEngine(clock=lambda: clock[0])
    context = {"trade_date": "2026-07-03", "missing_tickers": ["GONE", "MSNG"]}

    first = engine.from_breaches([_coverage_breach()], context)
    second = engine.from_breaches([_coverage_breach()], context)  # same key within cooldown
    assert len(first) == 1 and first[0].alert_id == "coverage_below_floor"
    assert second == []  # deduped, not a second alert


def test_cooldown_expiry_allows_the_alert_again() -> None:
    clock = [1000.0]
    engine = AlertEngine(clock=lambda: clock[0])
    context = {"trade_date": "2026-07-03"}
    assert engine.from_breaches([_coverage_breach()], context)
    clock[0] += 60 * 61  # past the 60-minute cooldown
    assert engine.from_breaches([_coverage_breach()], context)


def test_token_budget_alert_is_critical_and_keyed_on_run() -> None:
    engine = AlertEngine()
    breach = Breach("token_budget_run", "CRITICAL", "consumed_tokens", 61000, 60000, run_id="run-1")
    alerts = engine.from_breaches([breach], {"trade_date": "2026-07-03"})
    assert alerts and alerts[0].severity == "CRITICAL" and "run-1" in alerts[0].dedup_key


def test_breaker_open_event_deduped_per_provider() -> None:
    clock = [1000.0]
    engine = AlertEngine(clock=lambda: clock[0])
    first = engine.raise_event("breaker_open", {"provider": "groq"})
    again = engine.raise_event("breaker_open", {"provider": "groq"})
    other = engine.raise_event("breaker_open", {"provider": "gemini"})
    assert first is not None and again is None and other is not None


def test_unmapped_breach_raises_nothing() -> None:
    engine = AlertEngine()
    orphan = Breach("no_such_slo", "WARN", "whatever", 1, 0)
    assert engine.from_breaches([orphan], {"trade_date": "2026-07-03"}) == []
