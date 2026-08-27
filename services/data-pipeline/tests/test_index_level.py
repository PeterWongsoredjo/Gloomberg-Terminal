"""Index levels take their date from the row, never from the partition they landed in."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pipeline.bronze.feeds import FEEDS
from tests.fake_bronze import FakeMinio, land_live

FROZEN = Path(__file__).resolve().parents[1] / "fixtures" / "frozen"
INDEX_PART = (
    FROZEN / "idx_summary/index_level/ingest_date=2026-07-03/source_version=v1/part-0000.json"
)

INDEX_LEVEL = FEEDS["index_level"]
OLD = date(2026, 1, 5)
TODAY = date(2026, 8, 27)


def _levels(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"recordsTotal": len(rows), "data": rows}).encode()


def _row(code: str, when: Any, close: float = 100.0) -> dict[str, Any]:
    return {"IndexCode": code, "Date": when, "Close": close, "Previous": close, "Change": 0.0}


def _partitions(fake: FakeMinio) -> set[str]:
    return {k.split("/")[2] for k in fake.objects if k.startswith("idx_summary/index_level/")}


def test_the_endpoint_carries_no_date_so_the_feed_is_current_state() -> None:
    """The url has no date parameter, which is the whole reason for the capture-date rule."""
    assert INDEX_LEVEL.current_state is True
    assert INDEX_LEVEL.date_scoped is False
    assert "date=" not in INDEX_LEVEL.url


def test_a_historical_fetch_lands_under_the_capture_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked for 2026-01-05, captured 2026-08-27, so 2026-08-27 is where it belongs."""
    fake = FakeMinio()
    landed = land_live(
        monkeypatch, fake, INDEX_LEVEL, OLD,
        _levels([_row("COMPOSITE", "2026-08-27T00:00:00", 5875.78)]), captured_on=TODAY,
    )

    assert landed["trade_date"] == TODAY.isoformat()
    assert _partitions(fake) == {"ingest_date=2026-08-27"}


def test_a_historical_fetch_leaves_a_genuine_old_capture_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real 2026-01-05 capture is evidence, and a rerun today must not overwrite it."""
    fake = FakeMinio()
    land_live(
        monkeypatch, fake, INDEX_LEVEL, OLD,
        _levels([_row("COMPOSITE", "2026-01-05T00:00:00", 4000.0)]), captured_on=OLD,
    )
    genuine = dict(fake.objects)

    land_live(
        monkeypatch, fake, INDEX_LEVEL, OLD,
        _levels([_row("COMPOSITE", "2026-08-27T00:00:00", 5875.78)]), captured_on=TODAY,
    )

    for key, blob in genuine.items():
        assert fake.objects[key] == blob
    assert _partitions(fake) == {"ingest_date=2026-01-05", "ingest_date=2026-08-27"}


def test_a_same_day_capture_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The normal EOD path files under the trade date, exactly as it always did."""
    fake = FakeMinio()
    td = date(2026, 7, 3)
    landed = land_live(
        monkeypatch, fake, INDEX_LEVEL, td,
        _levels([_row("COMPOSITE", "2026-07-03T00:00:00", 5875.78)]), captured_on=td,
    )
    assert landed["trade_date"] == td.isoformat()
    assert _partitions(fake) == {"ingest_date=2026-07-03"}


def test_the_real_fixture_rows_carry_their_own_date() -> None:
    """Every row of the live-captured payload states the session it belongs to."""
    payload = json.loads(INDEX_PART.read_text(encoding="utf-8"))
    dates = {r["Date"] for r in payload["data"]}
    composite = next(r for r in payload["data"] if r["IndexCode"] == "COMPOSITE")

    assert dates == {"2026-07-03T00:00:00"}
    assert composite["Close"] == 5875.78


# --------------------------------------------- what rec.Date actually means, with evidence


def test_every_real_row_carries_a_bare_midnight_calendar_date() -> None:
    """No timezone marker and no time-of-day anywhere: this is a session date, not an instant."""
    payload = json.loads(INDEX_PART.read_text(encoding="utf-8"))
    stamps = {r["Date"] for r in payload["data"]}

    assert stamps == {"2026-07-03T00:00:00"}
    assert all(s.endswith("T00:00:00") for s in stamps)
    assert not any("Z" in s or "+" in s for s in stamps)


def test_the_date_scoped_sibling_echoes_the_requested_wib_session() -> None:
    """daily_trade asks for date=20260703 and every row answers 2026-07-03: a WIB calendar date."""
    trade = json.loads(
        (FROZEN / "idx_summary/daily_trade/ingest_date=2026-07-03/source_version=v1/part-0000.json")
        .read_text(encoding="utf-8")
    )
    assert {r["Date"] for r in trade["data"]} == {"2026-07-03T00:00:00"}
    assert "date={ymd}" in FEEDS["daily_trade"].url


def test_an_evening_stamp_must_not_roll_into_the_next_day() -> None:
    """The boundary a wrong +7h shift would break, kept in the adversarial fixture."""
    adversarial = json.loads(
        (Path(__file__).resolve().parents[1]
         / "fixtures/adversarial/idx_summary/index_level/ingest_date=2026-07-08/source_version=v1/part-0000.json")
        .read_text(encoding="utf-8")
    )
    late = next(r for r in adversarial["data"] if r["IndexCode"] == "IDXLATE")

    assert late["Date"] == "2026-07-03T20:00:00"
    # a UTC->WIB shift would make this 2026-07-04; the fact must stay on 2026-07-03
    assert date.fromisoformat(late["Date"][:10]) == date(2026, 7, 3)
