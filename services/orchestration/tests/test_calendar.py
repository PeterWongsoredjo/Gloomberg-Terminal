from datetime import date
from pathlib import Path

from orchestration.calendar import holiday_reason, is_trading_day

_CSV = """trade_date,is_trading_day,session_template,holiday_reason
2026-01-01,false,NORMAL,New Year's Day
2026-01-03,true,NORMAL,Replacement working Saturday
"""


def _seed(tmp_path: Path) -> Path:
    path = tmp_path / "ref_market_calendar.csv"
    path.write_text(_CSV, encoding="utf-8")
    return path


def test_normal_weekday_trades(tmp_path: Path) -> None:
    assert is_trading_day(date(2026, 1, 2), _seed(tmp_path)) is True  # Friday


def test_weekend_is_closed(tmp_path: Path) -> None:
    assert is_trading_day(date(2026, 1, 4), _seed(tmp_path)) is False  # Sunday


def test_holiday_override_closes_a_weekday(tmp_path: Path) -> None:
    assert is_trading_day(date(2026, 1, 1), _seed(tmp_path)) is False  # Thursday holiday


def test_working_saturday_override_opens_a_weekend(tmp_path: Path) -> None:
    assert is_trading_day(date(2026, 1, 3), _seed(tmp_path)) is True  # Saturday, forced open


def test_holiday_reason_reads_seed(tmp_path: Path) -> None:
    assert holiday_reason(date(2026, 1, 1), _seed(tmp_path)) == "New Year's Day"
    assert holiday_reason(date(2026, 1, 4), _seed(tmp_path)) == "weekend"
