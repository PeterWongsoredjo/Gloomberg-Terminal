"""Live-gated: exercises the intraday.news_item writer against the compose Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import psycopg
import pytest

from pipeline.config import get_settings

from orchestration.projection import prune, upsert_items

TD = date(2026, 7, 14)
_PREFIX = "testproj:"


def _dsn_or_skip() -> str:
    dsn = get_settings().postgres_dsn
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except psycopg.OperationalError:
        pytest.skip("postgres unavailable")
    return dsn


@pytest.fixture()
def dsn() -> Iterator[str]:
    value = _dsn_or_skip()
    yield value
    with psycopg.connect(value, autocommit=True) as conn:
        conn.execute("delete from intraday.news_item where item_id like %s", (f"{_PREFIX}%",))


def _item(suffix: str, tickers: list[str] | None = None) -> dict[str, Any]:
    return {
        "item_id": f"{_PREFIX}{suffix}",
        "source": "cnbc_market",
        "lang": "id",
        "title": f"headline {suffix}",
        "summary": "ringkasan",
        "url": f"https://example.test/{suffix}",
        "published_at": "2026-07-14T03:15:00+00:00",
        "tickers": tickers if tickers is not None else ["BBCA"],
    }


def test_first_upsert_returns_every_item(dsn: str) -> None:
    outcome = upsert_items(dsn, [_item("a"), _item("b", ["TLKM", "BBCA"])], TD, "ING1")
    assert sorted(outcome.new_item_ids) == [f"{_PREFIX}a", f"{_PREFIX}b"]
    assert outcome.new_tickers == ["BBCA", "TLKM"]


def test_reupsert_returns_nothing_new(dsn: str) -> None:
    upsert_items(dsn, [_item("a")], TD, "ING1")
    outcome = upsert_items(dsn, [_item("a"), _item("c")], TD, "ING2")
    assert outcome.new_item_ids == [f"{_PREFIX}c"]


def test_reupsert_never_touches_scoring_state(dsn: str) -> None:
    upsert_items(dsn, [_item("a")], TD, "ING1")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "update intraday.news_item set scored_at = now(), score_attempts = 2 where item_id = %s",
            (f"{_PREFIX}a",),
        )
    upsert_items(dsn, [_item("a")], TD, "ING2")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "select scored_at, score_attempts from intraday.news_item where item_id = %s",
            (f"{_PREFIX}a",),
        ).fetchone()
    assert row is not None and row[0] is not None and row[1] == 2


def test_prune_drops_only_old_rows(dsn: str) -> None:
    upsert_items(dsn, [_item("old")], date(2026, 6, 1), "ING1")
    upsert_items(dsn, [_item("new")], TD, "ING1")
    prune(dsn, TD, keep_days=14)
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "select item_id from intraday.news_item where item_id like %s", (f"{_PREFIX}%",)
        ).fetchall()
    assert [r[0] for r in rows] == [f"{_PREFIX}new"]
