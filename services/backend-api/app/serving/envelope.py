"""The one place a serving payload gets its freshness envelope.

Every REST and WebSocket payload passes through here so market_state and freshness are resolved
the same honest way everywhere: the calendar decides the session phase, the SLO engine decides
freshness. No endpoint fills these fields by hand.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TypeVar

from app.core.enums import QualityFlag, SessionPhase
from app.core.envelope import Envelope
from app.observability.calendar import SessionCalendar, get_calendar
from app.observability.slo.engine import SloEngine

DataT = TypeVar("DataT")

# the dataset the EOD freshness SLO is keyed on; price-derived payloads clock against it
DATASET_DAILY_TRADE = "idx_summary.daily_trade"


def build_envelope(
    data: DataT,
    *,
    data_as_of: datetime,
    trade_date: date,
    dataset: str | None = None,
    quality_flags: list[QualityFlag] | None = None,
    slo_engine: SloEngine | None = None,
    calendar: SessionCalendar | None = None,
    now_utc: datetime | None = None,
) -> Envelope[DataT]:
    """Wraps a value with a calendar-resolved phase and SLO-resolved freshness."""
    now = now_utc or datetime.now(UTC)
    cal = calendar or get_calendar()
    market_state = cal.market_state(trade_date, now)
    fresh = _freshness(dataset, data_as_of, trade_date, now, slo_engine, cal)
    return Envelope[DataT](
        served_at=now,
        data_as_of=data_as_of,
        freshness_slo_met=fresh,
        market_state=market_state,
        quality_flags=quality_flags or [],
        data=data,
    )


def _freshness(
    dataset: str | None,
    data_as_of: datetime,
    trade_date: date,
    now: datetime,
    slo_engine: SloEngine | None,
    calendar: SessionCalendar,
) -> bool:
    """A payload with no EOD-clocked dataset is always current; otherwise ask the SLO engine."""
    if dataset is None:
        return True
    engine = slo_engine or SloEngine(calendar=calendar)
    return engine.freshness_met(dataset, data_as_of, trade_date, now)


def market_state_now(trade_date: date, *, now_utc: datetime | None = None) -> SessionPhase:
    """The session phase for a date right now; used by the market/state endpoint and the tape."""
    return get_calendar().market_state(trade_date, now_utc or datetime.now(UTC))


def ct011_header(
    *,
    data_as_of: datetime,
    trade_date: date,
    quality_flags: list[QualityFlag] | None = None,
    dataset: str | None = None,
    slo_engine: SloEngine | None = None,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    """The envelope header fields alone, as a JSON dict, for frames that carry rows separately."""
    header = build_envelope(
        None,
        data_as_of=data_as_of,
        trade_date=trade_date,
        dataset=dataset,
        quality_flags=quality_flags,
        slo_engine=slo_engine,
        now_utc=now_utc,
    ).model_dump(mode="json")
    header.pop("data")
    return header
