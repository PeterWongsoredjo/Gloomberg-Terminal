"""What the reconcilers consider missing, given what Bronze already holds."""

from __future__ import annotations

import io
from datetime import date

import pytest

from pipeline.agentic import artifact_land
from pipeline.bronze.ingest import ingest_dates
from pipeline.bronze.news import unnormalized_dates
from tests.fake_bronze import FakeMinio


def _put(fake: FakeMinio, source: str, dataset: str, day: str) -> None:
    key = f"{source}/{dataset}/ingest_date={day}/source_version=v1/part-x-0000.json.zst"
    fake.put_object("b", key, io.BytesIO(b"x"))


def test_ingest_dates_reads_the_hive_partition() -> None:
    fake = FakeMinio()
    _put(fake, "news_rss", "cnbc_market", "2026-08-20")
    _put(fake, "news_rss", "cnbc_market", "2026-08-21")
    assert ingest_dates(fake, "news_rss/") == {date(2026, 8, 20), date(2026, 8, 21)}  # type: ignore[arg-type]


def test_ingest_dates_ignores_a_key_with_no_usable_date() -> None:
    fake = FakeMinio()
    fake.put_object("b", "news_rss/cnbc_market/ingest_date=not-a-date/part-x.json.zst", io.BytesIO(b"x"))
    assert ingest_dates(fake, "news_rss/") == set()  # type: ignore[arg-type]


def test_unnormalized_dates_is_captured_minus_parsed() -> None:
    fake = FakeMinio()
    for day in ("2026-08-20", "2026-08-21", "2026-08-24"):
        _put(fake, "news_rss", "cnbc_market", day)
    _put(fake, "news_rss", "items", "2026-08-21")
    assert unnormalized_dates(fake) == [date(2026, 8, 20), date(2026, 8, 24)]  # type: ignore[arg-type]


def test_unnormalized_dates_is_empty_when_every_day_was_parsed() -> None:
    fake = FakeMinio()
    _put(fake, "news_rss", "antara_ekonomi", "2026-08-20")
    _put(fake, "news_rss", "items", "2026-08-20")
    assert unnormalized_dates(fake) == []  # type: ignore[arg-type]


def test_unlanded_dates_is_the_ledger_minus_bronze(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMinio()
    _put(fake, "agent_artifact", "article_sentiment", "2026-09-08")
    ledger = {date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 10)}
    monkeypatch.setattr(artifact_land, "_ledger_dates", lambda settings: ledger)
    missing = artifact_land.unlanded_dates(fake, None)  # type: ignore[arg-type]
    assert missing == [date(2026, 9, 7), date(2026, 9, 10)]


def test_unlanded_dates_is_empty_when_every_scored_day_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMinio()
    _put(fake, "agent_artifact", "article_sentiment", "2026-09-08")
    monkeypatch.setattr(artifact_land, "_ledger_dates", lambda settings: {date(2026, 9, 8)})
    assert artifact_land.unlanded_dates(fake, None) == []  # type: ignore[arg-type]
