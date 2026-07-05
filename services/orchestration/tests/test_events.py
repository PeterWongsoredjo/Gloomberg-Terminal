from datetime import date, datetime, timezone

from orchestration.events import build_event

_T0 = datetime(2026, 7, 3, 9, 5, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 3, 9, 7, 11, tzinfo=timezone.utc)


def test_event_carries_ob01_correlation_anchors() -> None:
    event = build_event(
        flow_run_id="prefect-uuid",
        trade_date=date(2026, 7, 3),
        phase="dbt_build",
        status="SUCCESS",
        started_at=_T0,
        ended_at=_T1,
        ingest_run_id="01J9Z8",
        dbt_invocation_id="inv-42",
        run_id="run-7",
    )
    corr = event["correlation"]
    assert corr == {"ingest_run_id": "01J9Z8", "dbt_invocation_id": "inv-42", "run_id": "run-7"}
    assert event["trade_date"] == "2026-07-03"
    assert event["schema_version"] == "1.0.0"


def test_timestamps_are_utc_z_suffixed() -> None:
    event = build_event(
        flow_run_id="x",
        trade_date=date(2026, 7, 3),
        phase="guard",
        status="SUCCESS",
        started_at=_T0,
        ended_at=_T1,
    )
    assert event["started_at"] == "2026-07-03T09:05:00Z"
    assert event["ended_at"] == "2026-07-03T09:07:11Z"


def test_gate_is_present_only_when_provided() -> None:
    without = build_event(
        flow_run_id="x", trade_date=date(2026, 7, 3), phase="ingest",
        status="SUCCESS", started_at=_T0, ended_at=_T1,
    )
    assert "gate" not in without

    gate = {"coverage_ratio": 0.97, "promotion_ok": True}
    withgate = build_event(
        flow_run_id="x", trade_date=date(2026, 7, 3), phase="coverage_gate",
        status="PARTIAL", started_at=_T0, ended_at=_T1, gate=gate,
    )
    assert withgate["gate"] == gate
