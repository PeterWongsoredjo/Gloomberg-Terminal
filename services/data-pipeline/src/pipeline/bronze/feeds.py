"""
The one registry of upstream Bronze feeds, shared by fixture capture and live ingest.
"""

from __future__ import annotations

from dataclasses import dataclass

IDX = "https://www.idx.co.id/primary"


@dataclass(frozen=True)
class FeedSpec:
    """One upstream feed: where it comes from and how its payload lands in Bronze."""

    source: str
    dataset: str
    url: str
    source_version: str = "v1"
    ext: str = "json"
    is_universe: bool = False
    date_scoped: bool = False
    accumulates: bool = False


# the four live-tested JSON feeds plus the curated news RSS feeds
FEEDS: dict[str, FeedSpec] = {
    "daily_trade": FeedSpec(
        source="idx_summary",
        dataset="daily_trade",
        url=IDX + "/TradingSummary/GetStockSummary?length=1000&date={ymd}",
        is_universe=True,
        date_scoped=True,
    ),
    "index_level": FeedSpec(
        source="idx_summary",
        dataset="index_level",
        url=IDX + "/TradingSummary/GetIndexSummary",
    ),
    "corporate_actions": FeedSpec(
        source="corporate_actions",
        dataset="issued_history",
        url=IDX + "/ListingActivity/GetIssuedHistory?caType=&dateFrom=&dateTo=&start=0&length=9999",
    ),
    "company_profile": FeedSpec(
        source="company_profile",
        dataset="profiles",
        url=IDX + "/ListedCompany/GetCompanyProfiles?kodeEmiten=&emitenType=s&start=0&length=9999",
    ),
    "news_cnbc": FeedSpec(
        source="news_rss",
        dataset="cnbc_market",
        url="https://www.cnbcindonesia.com/market/rss",
        ext="xml",
        accumulates=True,
    ),
    "news_kontan": FeedSpec(
        source="news_rss",
        dataset="kontan_investasi",
        url="https://investasi.kontan.co.id/rss",
        ext="xml",
        accumulates=True,
    ),
    "news_antara": FeedSpec(
        source="news_rss",
        dataset="antara_ekonomi",
        url="https://www.antaranews.com/rss/ekonomi.xml",
        ext="xml",
        accumulates=True,
    ),
}

# the subset that a daily EOD flow ingests (news has its own lighter cadence)
EOD_FEEDS = [name for name, spec in FEEDS.items() if spec.source != "news_rss"]
NEWS_FEEDS = [name for name, spec in FEEDS.items() if spec.source == "news_rss"]
