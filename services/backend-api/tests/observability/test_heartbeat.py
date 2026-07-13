"""§3.9 heartbeat: a stale beat is the CRITICAL that fires when telemetry itself is dead."""

from __future__ import annotations

from app.observability.heartbeat import Heartbeat


def test_fresh_heartbeat_is_not_stale() -> None:
    clock = [0.0]
    hb = Heartbeat(interval_seconds=60, stale_after_seconds=180, clock=lambda: clock[0])
    hb.beat()
    clock[0] = 100.0
    assert not hb.is_stale()
    assert hb.check() is None


def test_missing_heartbeat_raises_critical() -> None:
    clock = [0.0]
    hb = Heartbeat(interval_seconds=60, stale_after_seconds=180, clock=lambda: clock[0])
    hb.beat()
    clock[0] = 500.0  # no beat for 500s, past the 180s staleness window
    assert hb.is_stale()
    alert = hb.check()
    assert alert is not None and alert["alert_id"] == "observability_heartbeat_dead"
    assert alert["severity"] == "CRITICAL"
