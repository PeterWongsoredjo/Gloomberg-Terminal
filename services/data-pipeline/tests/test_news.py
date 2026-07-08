"""Unit tests for the RSS news normalizer."""

from __future__ import annotations

from pipeline.bronze.news import parse_rss

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


def test_parse_rss_extracts_items_and_tickers() -> None:
    """Two items parse, the ticker candidate is found, IHSG is excluded."""
    items = parse_rss(_RSS, "cnbc_market")
    assert len(items) == 2
    first = items[0]
    assert first["source"] == "cnbc_market"
    assert "BBCA" in first["tickers"]
    assert "IHSG" not in first["tickers"]
    assert first["published_at"].endswith("+00:00")


def test_parse_rss_stable_item_id() -> None:
    """The item_id is a stable function of the source and link."""
    a = parse_rss(_RSS, "cnbc_market")[0]["item_id"]
    b = parse_rss(_RSS, "cnbc_market")[0]["item_id"]
    assert a == b and a.startswith("cnbc_market:")


def test_parse_rss_bad_xml_returns_empty() -> None:
    """A payload that is not XML yields no items instead of raising."""
    assert parse_rss(b"not xml at all", "cnbc_market") == []
