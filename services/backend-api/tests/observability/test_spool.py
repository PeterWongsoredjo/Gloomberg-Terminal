"""§3.9 spool: a full buffer sheds oldest low-severity first, never ERROR/CRITICAL."""

from __future__ import annotations

from datetime import date

from app.observability.events import Correlation, Measure, Severity, TelemetryEvent
from app.observability.spool import TelemetrySpool


def _event(severity: Severity, name: str = "coverage_ratio") -> TelemetryEvent:
    return TelemetryEvent(
        plane="PIPELINE",
        kind="coverage",
        correlation=Correlation(trade_date=date(2026, 7, 3), ingest_run_id="run-1"),
        measure=Measure(name=name, value=1, unit="ratio"),
        severity=severity,
    )


def test_full_buffer_drops_low_severity_and_counts_it() -> None:
    spool = TelemetrySpool(max_events=2)
    spool.emit(_event("INFO"))
    spool.emit(_event("WARN"))
    spool.emit(_event("ERROR"))  # buffer full -> shed the oldest INFO
    assert spool.dropped == 1
    assert len(spool) == 2


def test_error_and_critical_are_never_dropped() -> None:
    spool = TelemetrySpool(max_events=2)
    spool.emit(_event("ERROR"))
    spool.emit(_event("CRITICAL"))
    spool.emit(_event("CRITICAL"))  # nothing sheddable -> tolerate overflow, drop nothing
    assert spool.dropped == 0
    assert len(spool) == 3


def test_unknown_metric_is_refused_by_the_spool() -> None:
    spool = TelemetrySpool(max_events=10)
    assert spool.emit(_event("INFO", name="not_in_catalog")) is False
    assert len(spool) == 0


def test_drain_flushes_and_clears_on_success() -> None:
    spool = TelemetrySpool(max_events=10)
    spool.emit(_event("INFO"))
    spool.emit(_event("WARN"))
    drained = spool.drain(lambda batch: True)
    assert drained == 2
    assert len(spool) == 0


def test_drain_keeps_events_when_sink_fails() -> None:
    spool = TelemetrySpool(max_events=10)
    spool.emit(_event("INFO"))
    assert spool.drain(lambda batch: False) == 0
    assert len(spool) == 1
