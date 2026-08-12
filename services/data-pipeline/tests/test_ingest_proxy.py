"""Unit tests for scoping the Webshare proxy to Cloudflare-gated IDX feeds only."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from pipeline.bronze import ingest
from pipeline.bronze.feeds import FEEDS

TD = date(2026, 7, 14)


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
