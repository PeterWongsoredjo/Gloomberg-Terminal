"""Unit tests for scoping the Webshare proxy to Cloudflare-gated IDX feeds only."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from pipeline.bronze import ingest
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import FetchError, is_transient_fetch

TD = date(2026, 7, 14)


def test_a_proxied_403_is_worth_retrying() -> None:
    """Through a rotating proxy a 403 is a bad ip draw, and the next attempt differs."""
    assert is_transient_fetch(FetchError(403, "blocked", via_proxy=True))


def test_a_direct_403_is_a_real_block() -> None:
    """Straight from our own ip a 403 will not change on a retry."""
    assert not is_transient_fetch(FetchError(403, "blocked"))


def test_timeouts_and_server_errors_stay_transient() -> None:
    """These were retryable before the proxy existed and still are."""
    assert is_transient_fetch(FetchError(None, "timeout"))
    assert is_transient_fetch(FetchError(429, "rate limited"))
    assert is_transient_fetch(FetchError(503, "unavailable"))
    assert not is_transient_fetch(FetchError(404, "gone"))


def test_idx_feed_is_fetched_through_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A needs_proxy feed forwards the caller's proxy straight to fetch()."""
    seen: dict[str, Any] = {}

    def fake_fetch(url: str, *, proxy: str | None = None) -> bytes:
        seen["proxy"] = proxy
        return b"{}"

    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    monkeypatch.setattr(ingest, "land_payloads", lambda *a, **kw: {})

    spec = FEEDS["daily_trade"]
    assert spec.needs_proxy
    ingest.fetch_and_land(cast(Any, None), spec, TD, proxy="http://user:pass@p.webshare.io:80")

    assert seen["proxy"] == "http://user:pass@p.webshare.io:80"


def test_news_feed_never_uses_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A feed with needs_proxy=False ignores whatever proxy the caller has configured."""
    seen: dict[str, Any] = {}

    def fake_fetch(url: str, *, proxy: str | None = None) -> bytes:
        seen["proxy"] = proxy
        return b"<rss/>"

    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    monkeypatch.setattr(ingest, "land_payloads", lambda *a, **kw: {})

    spec = FEEDS["news_cnbc"]
    assert not spec.needs_proxy
    ingest.fetch_and_land(cast(Any, None), spec, TD, proxy="http://user:pass@p.webshare.io:80")

    assert seen["proxy"] is None
