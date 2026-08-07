"""Reading corporate actions out of the published Gold snapshot for scoring."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from pipeline.config import Settings, get_settings
from pipeline.gold.corporate_action_news import LOOKAHEAD_DAYS, LOOKBACK_DAYS, read_items, window_for

TD = date(2026, 7, 6)

_DDL = """
create table fct_corporate_action_news(
    item_id varchar, trade_date date, source varchar, lang varchar, title varchar,
    summary varchar, url varchar, published_at timestamp, tickers varchar[], item_type varchar
)
"""


def _settings(gold: Path) -> Settings:
    return replace(get_settings(), published_gold=gold)


def _snapshot(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    gold = tmp_path / "gold.duckdb"
    con = duckdb.connect(str(gold))
    con.execute(_DDL)
    for item_id, trade_date in rows:
        con.execute(
            "insert into fct_corporate_action_news values "
            f"('{item_id}','{trade_date}','idx_corporate_action','en','{item_id} split','s',"
            f"null,'{trade_date} 00:00:00',['AADI'],'CORPORATE_ACTION')"
        )
    con.close()
    return gold


def test_the_window_matches_the_gold_models_own_bounds() -> None:
    since, until = window_for(TD)
    assert (TD - since).days == LOOKBACK_DAYS
    assert (until - TD).days == LOOKAHEAD_DAYS


def test_items_come_back_shaped_for_the_projection(tmp_path: Path) -> None:
    """The reader hands the projection exactly the keys upsert_items reads."""
    gold = _snapshot(tmp_path, [("corp_action:idx_ca:1", "2026-07-10")])
    items = read_items(TD, _settings(gold))

    assert len(items) == 1
    assert items[0]["item_id"] == "corp_action:idx_ca:1"
    assert items[0]["item_type"] == "CORPORATE_ACTION"
    assert items[0]["url"] is None
    assert items[0]["tickers"] == ["AADI"]
    assert items[0]["candidate_tickers"] == []


def test_events_outside_the_window_are_not_queued(tmp_path: Path) -> None:
    """An old event must not be re-queued for scoring every single day."""
    gold = _snapshot(
        tmp_path,
        [
            ("corp_action:idx_ca:old", "2025-01-01"),
            ("corp_action:idx_ca:soon", "2026-07-10"),
            ("corp_action:idx_ca:far", "2026-09-01"),
        ],
    )
    assert [i["item_id"] for i in read_items(TD, _settings(gold))] == ["corp_action:idx_ca:soon"]


def test_an_unpublished_snapshot_yields_nothing(tmp_path: Path) -> None:
    """A box that has never promoted Gold degrades to no work, not a crash."""
    assert read_items(TD, _settings(tmp_path / "absent.duckdb")) == []


def test_a_snapshot_without_the_table_yields_nothing(tmp_path: Path) -> None:
    """The window between deploying this and the first rebuild must not fail the flow."""
    gold = tmp_path / "bare.duckdb"
    duckdb.connect(str(gold)).close()
    assert read_items(TD, _settings(gold)) == []


@pytest.mark.parametrize("trade_date", [date(2026, 7, 6), date(2026, 7, 7)])
def test_reading_is_read_only(tmp_path: Path, trade_date: date) -> None:
    """The snapshot has one writer, and this is not it."""
    gold = _snapshot(tmp_path, [("corp_action:idx_ca:1", "2026-07-10")])
    before = gold.stat().st_mtime_ns
    read_items(trade_date, _settings(gold))
    assert gold.stat().st_mtime_ns == before
