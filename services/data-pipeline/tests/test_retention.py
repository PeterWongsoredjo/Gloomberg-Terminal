from datetime import date, datetime, timedelta, timezone

from pipeline.bronze.retention import prune_candidates

TODAY = date(2026, 7, 19)
KEEP = 90


def _at(hour: int) -> datetime:
    return datetime(2026, 3, 1, hour, tzinfo=timezone.utc)


def _key(source: str, dataset: str, day: str, run: str) -> str:
    return f"_manifests/{source}/{dataset}/ingest_date={day}/{run}.json"


def test_keeps_newest_per_scope() -> None:
    old = "2026-01-05"
    objects = [
        (_key("idx_summary", "daily_trade", old, "A"), _at(9)),
        (_key("idx_summary", "daily_trade", old, "B"), _at(11)),
        (_key("idx_summary", "daily_trade", old, "C"), _at(10)),
    ]
    assert prune_candidates(objects, TODAY, KEEP) == [
        _key("idx_summary", "daily_trade", old, "A"),
        _key("idx_summary", "daily_trade", old, "C"),
    ]


def test_recent_dates_are_untouched() -> None:
    recent = (TODAY - timedelta(days=KEEP - 1)).isoformat()
    objects = [
        (_key("news_rss", "cnbc_market", recent, "A"), _at(9)),
        (_key("news_rss", "cnbc_market", recent, "B"), _at(10)),
    ]
    assert prune_candidates(objects, TODAY, KEEP) == []


def test_scopes_are_independent() -> None:
    old = "2026-01-05"
    objects = [
        (_key("idx_summary", "daily_trade", old, "A"), _at(9)),
        (_key("idx_summary", "index_level", old, "B"), _at(10)),
    ]
    assert prune_candidates(objects, TODAY, KEEP) == []


def test_ignores_non_manifest_and_malformed_keys() -> None:
    objects = [
        ("idx_summary/daily_trade/ingest_date=2026-01-05/part-A-0000.json.zst", _at(9)),
        ("_manifests/idx_summary/daily_trade/oops/A.json", _at(9)),
        ("_manifests/idx_summary/daily_trade/ingest_date=notadate/A.json", _at(9)),
    ]
    assert prune_candidates(objects, TODAY, KEEP) == []


def test_empty_listing_is_safe() -> None:
    assert prune_candidates([], TODAY, KEEP) == []
