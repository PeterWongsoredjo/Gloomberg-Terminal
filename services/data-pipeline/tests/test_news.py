"""Unit tests for the RSS news normalizer."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, cast

import pytest

from pipeline.bronze import news
from pipeline.bronze.feeds import FEEDS, NEWS_FEEDS
from pipeline.bronze.news import day_items, parse_rss
from pipeline.reference.matcher import Registry
from pipeline.reference.securities import load_baseline

if TYPE_CHECKING:
    from minio import Minio

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CNBC Market</title>
    <item>
      <title>BBCA cetak laba, IHSG menguat</title>
      <description>Bank BCA (BBCA) membukukan laba bersih naik.</description>
      <link>https://example.com/bbca-laba</link>
      <pubDate>Fri, 03 Jul 2026 09:15:00 +0700</pubDate>
    </item>
    <item>
      <title>Berita tanpa kode saham</title>
      <description>Tidak ada emiten disebut di sini.</description>
      <link>https://example.com/no-ticker</link>
      <pubDate>Fri, 03 Jul 2026 08:00:00 +0700</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_extracts_items_and_claims_no_issuer() -> None:
    """Two items parse, and the parser names no issuer at all."""
    items = parse_rss(_RSS, "cnbc_market")
    assert len(items) == 2
    first = items[0]
    assert first["source"] == "cnbc_market"
    assert first["published_at"].endswith("+00:00")
    assert "tickers" not in first  # deciding the issuer is the registry's job
    assert "candidate_tickers" not in first


def test_parse_rss_never_names_an_issuer_even_when_a_code_is_present() -> None:
    """A headline full of four-letter jargon still yields no ticker guess."""
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item>
      <title>LQ45 turun jelang RUPS emiten</title>
      <description>Belum ada HMETD yang diumumkan.</description>
      <link>https://example.com/lq45</link>
      <pubDate>Fri, 03 Jul 2026 09:15:00 +0700</pubDate>
    </item></channel></rss>"""
    item = parse_rss(rss, "cnbc_market")[0]
    assert not any(key.endswith("tickers") for key in item)


def test_tag_items_resolves_against_the_registry() -> None:
    """Tagging is what turns candidates into tickers, dropping the ones IDX never listed."""
    items = news.tag_items(parse_rss(_RSS, "cnbc_market"), Registry(load_baseline()))
    assert items[0]["tickers"] == ["BBCA", "IHSG"]
    assert items[1]["tickers"] == []


def test_news_feeds_registry_dropped_cnbc_all() -> None:
    """The curated registry carries exactly the three quality feeds."""
    datasets = {FEEDS[name].dataset for name in NEWS_FEEDS}
    assert datasets == {"cnbc_market", "kontan_investasi", "antara_ekonomi"}


def test_retired_datasets_go_dark_immediately() -> None:
    """Already-landed payloads from a removed feed stop being parsed."""
    assert "cnbc_all" not in news._ACTIVE_DATASETS
    assert news._ACTIVE_DATASETS == {"cnbc_market", "kontan_investasi", "antara_ekonomi"}


def test_parse_rss_stable_item_id() -> None:
    """The item_id is a stable function of the source and link."""
    a = parse_rss(_RSS, "cnbc_market")[0]["item_id"]
    b = parse_rss(_RSS, "cnbc_market")[0]["item_id"]
    assert a == b and a.startswith("cnbc_market:")


def test_parse_rss_bad_xml_returns_empty() -> None:
    """A payload that is not XML yields no items instead of raising."""
    assert parse_rss(b"not xml at all", "cnbc_market") == []


def test_day_items_dedups_across_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same payload landed twice yields each item exactly once, sorted."""
    monkeypatch.setattr(
        news, "_read_raw", lambda minio, td: [("cnbc_market", _RSS), ("cnbc_market", _RSS)]
    )
    items = day_items(cast("Minio", None), date(2026, 7, 3))
    ids = [i["item_id"] for i in items]
    assert len(ids) == 2 and ids == sorted(ids) and len(set(ids)) == 2


class _StubObject:
    def __init__(self, name: str) -> None:
        self.object_name = name


class _StubMinio:
    """Records every prefix asked for, so the test can assert what was requested."""

    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self.prefixes: list[str] = []

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False):
        self.prefixes.append(prefix)
        return [_StubObject(k) for k in self._keys if k.startswith(prefix)]


def test_read_raw_asks_the_store_for_one_day_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The date lives in the prefix, so listing cost stays flat as history grows."""
    keys = [
        "news_rss/cnbc_market/ingest_date=2026-07-03/source_version=v1/part-A-0000.xml.zst",
        "news_rss/cnbc_market/ingest_date=2020-01-01/source_version=v1/part-OLD-0000.xml.zst",
        "news_rss/items/ingest_date=2026-07-03/source_version=v1/part-B-0000.json.zst",
    ]
    stub = _StubMinio(keys)
    monkeypatch.setattr(news, "read_object", lambda minio, name: b"<rss></rss>")

    got = news._read_raw(cast("Minio", stub), date(2026, 7, 3))

    assert [d for d, _ in got] == ["cnbc_market"]
    assert all("ingest_date=2026-07-03" in p for p in stub.prefixes)
    assert not any(p == "news_rss/" for p in stub.prefixes)


def test_read_raw_never_lists_the_whole_feed_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Years of old polls cost nothing, because the store never returns them."""
    old = [
        f"news_rss/cnbc_market/ingest_date=2020-01-{d:02d}/source_version=v1/part-{d}-0000.xml.zst"
        for d in range(1, 29)
    ]
    today = ["news_rss/cnbc_market/ingest_date=2026-07-03/source_version=v1/part-NEW-0000.xml.zst"]
    stub = _StubMinio(old + today)
    monkeypatch.setattr(news, "read_object", lambda minio, name: b"<rss></rss>")

    assert len(news._read_raw(cast("Minio", stub), date(2026, 7, 3))) == 1


def test_landed_news_carries_no_issuer_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """What reaches Bronze is what the publisher sent, never what we concluded."""
    landed: dict[str, object] = {}
    monkeypatch.setattr(news, "_read_raw", lambda minio, td: [("cnbc_market", _RSS)])
    monkeypatch.setattr(
        news,
        "land_payloads",
        lambda minio, **kwargs: landed.update(kwargs) or {"record_count": kwargs["record_count"]},
    )
    news.normalize_from_bronze(cast("Minio", None), date(2026, 7, 3))
    items = json.loads(cast(list, landed["payloads"])[0])
    assert items and all(not key.endswith("tickers") for item in items for key in item)
