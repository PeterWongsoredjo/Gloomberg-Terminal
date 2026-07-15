from datetime import date, datetime, timezone
from pathlib import Path

from orchestration.config import OrchestrationConfig
from orchestration.errors import TriggerPermanentError, TriggerTransientError
from orchestration.flow_intraday import (
    _degrade_on_projection_failure,
    _degrade_on_trigger_failure,
    ingest_news_result,
    scoring_universe,
)
from orchestration.phases import rollup
from orchestration.tasks.session_guard import guard_open_session

_CSV = """trade_date,is_trading_day,session_template,holiday_reason
2026-01-01,false,NORMAL,New Year's Day
"""

_YAML = """timezone_offset_hours: 7
templates:
  NORMAL:
    close_time: "16:00"
    eod_freshness_minutes: 90
    phases:
      - ["09:00", "12:00", "SESSION_1"]
      - ["13:30", "15:50", "SESSION_2"]
"""


def _config(tmp_path: Path) -> OrchestrationConfig:
    seed = tmp_path / "cal.csv"
    seed.write_text(_CSV, encoding="utf-8")
    windows = tmp_path / "windows.yaml"
    windows.write_text(_YAML, encoding="utf-8")
    return OrchestrationConfig(
        dbt_dir=tmp_path,
        calendar_seed=seed,
        coverage_floor=0.95,
        coverage_hard_min=0.80,
        ingest_mode="live",
        backend_api_url="http://test",
        backend_api_token="",
        poll_interval_seconds=0.0,
        poll_timeout_seconds=1.0,
        trigger_timeout_seconds=1.0,
        eod_cron="0 17 * * 1-5",
        objective="daily_sentiment",
        session_windows=windows,
    )


def _manifest(status: str) -> dict[str, str]:
    return {"status": status, "ingest_run_id": f"RUN_{status}"}


def test_ingest_all_landed_is_success() -> None:
    result = ingest_news_result([_manifest("SUCCESS")] * 4)
    assert result.status == "SUCCESS"
    assert result.ingest_run_id == "RUN_SUCCESS"


def test_ingest_some_landed_is_partial() -> None:
    result = ingest_news_result([_manifest("SUCCESS"), _manifest("FAILED")])
    assert result.status == "PARTIAL"


def test_ingest_none_landed_is_failed() -> None:
    result = ingest_news_result([_manifest("FAILED"), _manifest("FAILED")])
    assert result.status == "FAILED"
    assert result.ingest_run_id is None


def test_universe_comes_from_new_items() -> None:
    payload = {"new_items": ["a", "b"], "tickers": ["BBCA", "TLKM"]}
    assert scoring_universe(payload, 16) == ["BBCA", "TLKM"]


def test_universe_is_capped() -> None:
    payload = {"new_items": ["a"], "tickers": [f"TIC{i}" for i in range(20)]}
    assert len(scoring_universe(payload, 16)) == 16


def test_universe_empty_without_new_items() -> None:
    assert scoring_universe({"new_items": [], "tickers": ["BBCA"]}, 16) == []
    assert scoring_universe(None, 16) == []


def test_projection_failure_degrades() -> None:
    result = _degrade_on_projection_failure(RuntimeError("pg down"))
    assert result is not None and result.status == "DEGRADED"


def test_trigger_failure_degrades_only_typed_errors() -> None:
    assert _degrade_on_trigger_failure(TriggerTransientError("x")) is not None
    assert _degrade_on_trigger_failure(TriggerPermanentError("x")) is not None
    assert _degrade_on_trigger_failure(RuntimeError("x")) is None


def test_rollup_prefers_most_severe() -> None:
    assert rollup("SUCCESS", "SKIPPED") == "SUCCESS"
    assert rollup("PARTIAL", "DEGRADED", "SUCCESS") == "DEGRADED"
    assert rollup("FAILED", "DEGRADED") == "FAILED"


def test_guard_skips_holiday(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    result = guard_open_session.fn(date(2026, 1, 1), now, _config(tmp_path))
    assert result.status == "SKIPPED" and result.payload is False
    assert "non-trading" in result.notes


def test_guard_skips_outside_session(tmp_path: Path) -> None:
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)  # 19:00 WIB
    result = guard_open_session.fn(date(2026, 1, 2), now, _config(tmp_path))
    assert result.status == "SKIPPED"
    assert "outside session" in result.notes


def test_guard_passes_in_session_one(tmp_path: Path) -> None:
    now = datetime(2026, 1, 2, 3, 0, tzinfo=timezone.utc)  # 10:00 WIB
    result = guard_open_session.fn(date(2026, 1, 2), now, _config(tmp_path))
    assert result.status == "SUCCESS" and result.payload is True


def test_guard_fails_closed_on_missing_windows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.session_windows.unlink()
    result = guard_open_session.fn(date(2026, 1, 2), datetime.now(timezone.utc), config)
    assert result.status == "SKIPPED"
    assert "failing closed" in result.notes
