"""Unit tests for the feed registry and its date-window urls."""

from __future__ import annotations

from datetime import date

from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import url_for

TD = date(2026, 8, 12)


def test_no_feed_lands_binary_bytes() -> None:
    """record_count reads every feed's payload, so no feed may carry bytes it cannot parse."""
    assert {spec.ext for spec in FEEDS.values()} == {"json", "xml"}


def test_dividend_feed_never_gates_gold_promotion() -> None:
    """A sparse feed marked is_universe could drag coverage below the hard floor."""
    assert not FEEDS["dividend_announcements"].is_universe


def test_dividend_feed_replaces_on_a_rerun() -> None:
    """A deterministic run id is what lets the pdf stage rebuild this key without listing."""
    assert not FEEDS["dividend_announcements"].accumulates


def test_url_for_fills_the_lookback_window() -> None:
    """The dividend window starts lookback_days before the trade date and ends on it."""
    url = url_for(FEEDS["dividend_announcements"], TD)
    assert "dateFrom=20260805" in url
    assert "dateTo=20260812" in url


def test_url_for_dates_a_single_day_feed() -> None:
    """A date_scoped feed with no lookback asks only for its own trade date."""
    assert "date=20260812" in url_for(FEEDS["daily_trade"], TD)


def test_url_for_leaves_an_undated_feed_alone() -> None:
    """A feed that takes no date placeholder is passed through untouched."""
    spec = FEEDS["index_level"]
    assert url_for(spec, TD) == spec.url
