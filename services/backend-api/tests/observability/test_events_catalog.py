"""OB-01/OB-02 + Appendix A: events need an anchor, and unknown metrics are dropped."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.observability.catalog import accept, is_known
from app.observability.events import Correlation, Measure, TelemetryEvent


def _event(**correlation: object) -> TelemetryEvent:
    return TelemetryEvent(
        plane="PIPELINE",
        kind="coverage",
        correlation=Correlation(**correlation),  # type: ignore[arg-type]
        measure=Measure(name="coverage_ratio", value=0.9, unit="ratio"),
    )


def test_event_needs_trade_date_and_anchor() -> None:
    ok = _event(trade_date=date(2026, 7, 3), ingest_run_id="run-1")
    assert ok.correlation.ingest_run_id == "run-1"


def test_event_without_trade_date_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(ingest_run_id="run-1")


def test_non_meta_event_without_anchor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _event(trade_date=date(2026, 7, 3))


def test_meta_event_may_be_anchorless_but_dated() -> None:
    beat = TelemetryEvent(
        plane="META",
        kind="heartbeat",
        correlation=Correlation(trade_date=date(2026, 7, 3)),
        measure=Measure(name="heartbeat", value=True, unit="bool"),
    )
    assert beat.plane == "META"


def test_catalog_accepts_known_and_drops_unknown() -> None:
    assert is_known("coverage_ratio")
    assert accept("coverage_ratio") is True
    assert accept("totally_made_up_metric") is False
