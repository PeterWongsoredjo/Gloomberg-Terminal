"""OB-05 SLO engine: freshness is calendar-aware; coverage and budget breaches fire correctly."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.observability.calendar import SessionCalendar
from app.observability.slo.engine import SloEngine, SloSample

_ENGINE = SloEngine()
_CAL = SessionCalendar()

# 2026-07-03 is a trading Friday; close is 16:00 WIB = 09:00 UTC
_CLOSE_UTC = _CAL.close_datetime_utc(date(2026, 7, 3))
_AFTER_DEADLINE = _CLOSE_UTC + timedelta(minutes=200)


def test_weekend_never_raises_a_freshness_breach() -> None:
    saturday = date(2026, 7, 4)
    sample = SloSample(trade_date=saturday, now_utc=datetime(2026, 7, 4, 12, tzinfo=UTC), freshness={})
    assert not [b for b in _ENGINE.evaluate(sample) if b.measure == "data_as_of_age"]


def test_holiday_never_raises_a_freshness_breach() -> None:
    independence_day = date(2026, 8, 17)  # seed marks this non-trading
    sample = SloSample(trade_date=independence_day, now_utc=datetime(2026, 8, 17, 12, tzinfo=UTC), freshness={})
    assert not [b for b in _ENGINE.evaluate(sample) if b.measure == "data_as_of_age"]


def test_freshness_not_due_before_the_deadline() -> None:
    sample = SloSample(trade_date=date(2026, 7, 3), now_utc=_CLOSE_UTC, freshness={})
    assert _ENGINE.freshness_met("idx_summary.daily_trade", None, date(2026, 7, 3), _CLOSE_UTC)
    assert not [b for b in _ENGINE.evaluate(sample) if b.measure == "data_as_of_age"]


def test_stale_data_breaches_after_the_deadline() -> None:
    sample = SloSample(trade_date=date(2026, 7, 3), now_utc=_AFTER_DEADLINE, freshness={})
    breaches = [b for b in _ENGINE.evaluate(sample) if b.slo_id == "freshness_eod_daily_trade"]
    assert breaches and breaches[0].severity == "WARN"
    assert not _ENGINE.freshness_met("idx_summary.daily_trade", None, date(2026, 7, 3), _AFTER_DEADLINE)


def test_fresh_data_after_deadline_meets_slo() -> None:
    landed = _CLOSE_UTC + timedelta(minutes=30)
    sample = SloSample(
        trade_date=date(2026, 7, 3),
        now_utc=_AFTER_DEADLINE,
        freshness={"idx_summary.daily_trade": landed},
    )
    assert not [b for b in _ENGINE.evaluate(sample) if b.slo_id == "freshness_eod_daily_trade"]
    assert _ENGINE.freshness_met("idx_summary.daily_trade", landed, date(2026, 7, 3), _AFTER_DEADLINE)


def test_ramadan_earlier_close_shifts_the_freshness_deadline() -> None:
    # a fake calendar with a much earlier close proves the deadline follows the session template
    class _Ramadan(SessionCalendar):
        def is_trading_day(self, day: date) -> bool:
            return True

        def close_datetime_utc(self, day: date) -> datetime:
            return datetime(2026, 7, 3, 8, tzinfo=UTC)  # 15:00 WIB Ramadan close

    engine = SloEngine(calendar=_Ramadan())
    early_close = datetime(2026, 7, 3, 8, tzinfo=UTC)
    just_after = early_close + timedelta(minutes=100)  # past the 90m window on the Ramadan close
    sample = SloSample(trade_date=date(2026, 7, 3), now_utc=just_after, freshness={})
    assert [b for b in engine.evaluate(sample) if b.slo_id == "freshness_eod_daily_trade"]


def test_coverage_below_floor_raises_one_error() -> None:
    sample = SloSample(trade_date=date(2026, 7, 3), now_utc=_AFTER_DEADLINE, coverage_ratio=0.6)
    breaches = [b for b in _ENGINE.evaluate(sample) if b.slo_id == "coverage_floor"]
    assert len(breaches) == 1 and breaches[0].severity == "ERROR"


def test_token_budget_breach_is_critical_and_carries_run_id() -> None:
    sample = SloSample(trade_date=date(2026, 7, 3), consumed_tokens=61000, run_id="run-xyz")
    breaches = [b for b in _ENGINE.evaluate(sample) if b.slo_id == "token_budget_run"]
    assert breaches and breaches[0].severity == "CRITICAL" and breaches[0].run_id == "run-xyz"


def test_provider_quota_breach_per_provider() -> None:
    sample = SloSample(trade_date=date(2026, 7, 3), provider_quota={"groq": 1.0, "gemini": 0.5})
    breaches = [b for b in _ENGINE.evaluate(sample) if b.slo_id == "provider_quota"]
    assert len(breaches) == 1 and breaches[0].provider == "groq"
