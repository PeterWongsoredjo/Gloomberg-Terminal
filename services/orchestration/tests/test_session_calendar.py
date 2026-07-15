from datetime import date, datetime, timezone
from pathlib import Path

from orchestration.calendar import (
    OPEN_SESSIONS,
    load_session_windows,
    session_phase,
    session_template_name,
)

_CSV = """trade_date,is_trading_day,session_template,holiday_reason
2026-01-01,false,NORMAL,New Year's Day
2026-03-20,true,RAMADAN,
2026-12-31,true,HALF_DAY,
"""

_YAML = """timezone_offset_hours: 7
templates:
  NORMAL:
    close_time: "16:00"
    eod_freshness_minutes: 90
    phases:
      - ["09:00", "12:00", "SESSION_1"]
      - ["12:00", "13:30", "SESSION_BREAK"]
      - ["13:30", "15:50", "SESSION_2"]
  HALF_DAY:
    close_time: "11:30"
    eod_freshness_minutes: 90
    phases:
      - ["09:00", "11:30", "SESSION_1"]
"""


def _seed(tmp_path: Path) -> Path:
    path = tmp_path / "ref_market_calendar.csv"
    path.write_text(_CSV, encoding="utf-8")
    return path


def _windows(tmp_path: Path, text: str = _YAML) -> Path:
    path = tmp_path / "session_windows.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _utc(hour: int, minute: int) -> datetime:
    # WIB is UTC+7, so 09:00 WIB is 02:00 UTC
    return datetime(2026, 1, 2, hour, minute, tzinfo=timezone.utc)


def test_template_defaults_to_normal(tmp_path: Path) -> None:
    assert session_template_name(date(2026, 1, 2), _seed(tmp_path)) == "NORMAL"
    assert session_template_name(date(2026, 12, 31), _seed(tmp_path)) == "HALF_DAY"


def test_session_one_boundaries(tmp_path: Path) -> None:
    seed, windows = _seed(tmp_path), _windows(tmp_path)
    day = date(2026, 1, 2)
    assert session_phase(day, _utc(2, 0), seed, windows) == "SESSION_1"
    assert session_phase(day, _utc(4, 59), seed, windows) == "SESSION_1"
    assert session_phase(day, _utc(5, 0), seed, windows) == "SESSION_BREAK"


def test_session_two_boundaries(tmp_path: Path) -> None:
    seed, windows = _seed(tmp_path), _windows(tmp_path)
    day = date(2026, 1, 2)
    assert session_phase(day, _utc(6, 30), seed, windows) == "SESSION_2"
    assert session_phase(day, _utc(8, 49), seed, windows) == "SESSION_2"
    assert session_phase(day, _utc(8, 50), seed, windows) == "CLOSED"


def test_before_open_is_closed(tmp_path: Path) -> None:
    seed, windows = _seed(tmp_path), _windows(tmp_path)
    assert session_phase(date(2026, 1, 2), _utc(1, 0), seed, windows) == "CLOSED"


def test_half_day_template_shortens_the_session(tmp_path: Path) -> None:
    seed, windows = _seed(tmp_path), _windows(tmp_path)
    day = date(2026, 12, 31)
    afternoon = datetime(2026, 12, 31, 7, 0, tzinfo=timezone.utc)
    assert session_phase(day, afternoon, seed, windows) == "CLOSED"


def test_unknown_template_fails_closed(tmp_path: Path) -> None:
    seed, windows = _seed(tmp_path), _windows(tmp_path)
    assert session_phase(date(2026, 3, 20), _utc(3, 0), seed, windows) is None


def test_garbled_yaml_fails_closed(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    windows = _windows(tmp_path, "templates: [not, a, mapping")
    assert load_session_windows(windows) is None
    assert session_phase(date(2026, 1, 2), _utc(3, 0), seed, windows) is None


def test_missing_yaml_fails_closed(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    missing = tmp_path / "nope.yaml"
    assert session_phase(date(2026, 1, 2), _utc(3, 0), seed, missing) is None


def test_open_sessions_constant() -> None:
    assert OPEN_SESSIONS == {"SESSION_1", "SESSION_2"}
