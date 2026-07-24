from datetime import date

from pipeline.bronze import paths
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import run_id_for
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key

TD = date(2026, 7, 14)


def test_eod_feed_id_is_deterministic() -> None:
    spec = FEEDS["daily_trade"]
    expected = deterministic_run_id(
        idempotency_key(spec.source, spec.dataset, TD, spec.source_version)
    )
    assert run_id_for(spec, TD) == expected
    assert run_id_for(spec, TD) == run_id_for(spec, TD)


def test_eod_feed_id_varies_by_date() -> None:
    spec = FEEDS["company_profile"]
    assert run_id_for(spec, TD) != run_id_for(spec, date(2026, 7, 15))


def test_news_feed_id_is_fresh_per_call() -> None:
    spec = FEEDS["news_cnbc"]
    assert spec.accumulates
    assert run_id_for(spec, TD) != run_id_for(spec, TD)


def test_all_news_feeds_accumulate_and_eod_feeds_replace() -> None:
    for spec in FEEDS.values():
        assert spec.accumulates == (spec.source == "news_rss")


def test_failed_and_success_manifests_share_one_key_per_eod_scope() -> None:
    spec = FEEDS["index_level"]
    key = paths.manifest_key(spec.source, spec.dataset, TD, run_id_for(spec, TD))
    again = paths.manifest_key(spec.source, spec.dataset, TD, run_id_for(spec, TD))
    assert key == again
