"""The quarantine count reads the published Gold, one trade date at a time."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from pipeline.gold.quarantine import quarantined_rows


def _gold(tmp_path: Path) -> Path:
    path = tmp_path / "gold.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "create table agg_pipeline_telemetry(source varchar, dataset varchar, trade_date date, "
        "record_count bigint, quarantine_count bigint)"
    )
    con.execute(
        "insert into agg_pipeline_telemetry values "
        "('idx_summary','daily_trade','2026-07-03',957,2),"
        "('company_profile','profiles','2026-07-03',960,1),"
        "('idx_summary','index_level','2026-07-03',40,0),"
        "('idx_summary','daily_trade','2026-07-02',955,9)"
    )
    con.close()
    return path


def test_counts_sum_across_datasets_for_the_date(tmp_path: Path) -> None:
    assert quarantined_rows(_gold(tmp_path), date(2026, 7, 3)) == 3


def test_a_clean_or_missing_date_is_zero_not_none(tmp_path: Path) -> None:
    assert quarantined_rows(_gold(tmp_path), date(2026, 7, 10)) == 0


def test_a_missing_snapshot_raises_rather_than_reporting_zero(tmp_path: Path) -> None:
    """Fail closed: no snapshot is not the same as no quarantined rows."""
    with pytest.raises(duckdb.Error):
        quarantined_rows(tmp_path / "absent.duckdb", date(2026, 7, 3))
