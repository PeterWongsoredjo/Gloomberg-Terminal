"""The envelope builder resolves session phase and freshness the same way for every endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.core.enums import SessionPhase
from app.observability.calendar import get_calendar
from app.serving.envelope import DATASET_DAILY_TRADE, build_envelope

_TRADING_DAY = date(2026, 7, 2)  # Thursday
_WEEKEND = date(2026, 7, 4)  # Saturday


def test_weekend_is_closed_and_not_stale() -> None:
    """A closed market is CLOSED and never flagged stale, whatever the data age."""
    close = get_calendar().close_datetime_utc(_WEEKEND)
    env = build_envelope(
        {"x": 1},
        data_as_of=close - timedelta(days=3),
        trade_date=_WEEKEND,
        dataset=DATASET_DAILY_TRADE,
        now_utc=close + timedelta(hours=5),
    )
    assert env.market_state is SessionPhase.CLOSED
    assert env.freshness_slo_met is True


def test_stale_when_data_predates_close_after_deadline() -> None:
    """On a trading day, data from before the close read after the deadline is not fresh."""
    close = get_calendar().close_datetime_utc(_TRADING_DAY)
    env = build_envelope(
        {"x": 1},
        data_as_of=close - timedelta(hours=1),
        trade_date=_TRADING_DAY,
        dataset=DATASET_DAILY_TRADE,
        now_utc=close + timedelta(hours=3),
    )
    assert env.freshness_slo_met is False


def test_fresh_when_data_landed_after_close() -> None:
    """Data stamped at or after the close is fresh."""
    close = get_calendar().close_datetime_utc(_TRADING_DAY)
    env = build_envelope(
        {"x": 1},
        data_as_of=close + timedelta(minutes=10),
        trade_date=_TRADING_DAY,
        dataset=DATASET_DAILY_TRADE,
        now_utc=close + timedelta(hours=3),
    )
    assert env.freshness_slo_met is True


def test_no_dataset_is_always_fresh() -> None:
    """A payload with no EOD clock (sentiment, news) carries no freshness SLO."""
    env = build_envelope(
        {"x": 1},
        data_as_of=datetime(2020, 1, 1, tzinfo=UTC),
        trade_date=_TRADING_DAY,
        now_utc=datetime.now(UTC),
    )
    assert env.freshness_slo_met is True


def test_session_phase_resolves_intraday() -> None:
    """10:00 WIB on a trading day is Session 1."""
    env = build_envelope(
        {"x": 1},
        data_as_of=datetime(2026, 7, 2, 3, 0, tzinfo=UTC),
        trade_date=_TRADING_DAY,
        now_utc=datetime(2026, 7, 2, 3, 0, tzinfo=UTC),
    )
    assert env.market_state is SessionPhase.SESSION_1
