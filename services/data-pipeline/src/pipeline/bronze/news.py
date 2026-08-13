"""
Normalizes raw RSS news into the JSON news items the sentiment flow reads.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from minio import Minio

from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import client, land_payloads, read_object
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.config import BRONZE_BUCKET, Settings, get_settings
from pipeline.reference.matcher import Registry
from pipeline.reference.store import read as read_registry

_CODE = re.compile(r"\b[A-Z]{4}\b")

_ACTIVE_DATASETS = {spec.dataset for spec in FEEDS.values() if spec.source == "news_rss"}


def _text(item: ElementTree.Element, tag: str) -> str:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] == tag and child.text:
            return child.text.strip()
    return ""


def _published_at(raw: str) -> str:
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _code_candidates(*texts: str) -> list[str]:
    """Raw 4-letter tokens in the order they appear; the registry decides which are real."""
    seen: dict[str, None] = {}
    for text in texts:
        for code in _CODE.findall(text):
            seen.setdefault(code, None)
    return list(seen)


def parse_rss(xml: bytes, source_dataset: str) -> list[dict[str, Any]]:
    """Parses one RSS payload into normalized news item dicts."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    items = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "item":
            continue
        title = _text(node, "title")
        summary = _text(node, "description")
        link = _text(node, "link")
        item_id = f"{source_dataset}:{hashlib.sha1(link.encode()).hexdigest()[:16]}"
        items.append(
            {
                "item_id": item_id,
                "source": source_dataset,
                "lang": "id",
                "title": title,
                "summary": summary,
                "url": link,
                "published_at": _published_at(_text(node, "pubDate")),
                "candidate_tickers": _code_candidates(title, summary),
            }
        )
    return items


def tag_items(items: list[dict[str, Any]], registry: Registry) -> list[dict[str, Any]]:
    """Resolves each item's real tickers against the registry, names and codes alike."""
    for item in items:
        match = registry.match(str(item["title"]), str(item.get("summary") or ""))
        item["tickers"] = match.tickers
        item["candidate_tickers"] = match.candidates
    return items


def _read_raw(minio: Minio, trade_date: date) -> list[tuple[str, bytes]]:
    prefix = "news_rss/"
    out = []
    for obj in minio.list_objects(BRONZE_BUCKET, prefix=prefix, recursive=True):
        name = obj.object_name
        if "/items/" in name or f"ingest_date={trade_date.isoformat()}" not in name or not name.endswith(".zst"):
            continue
        dataset = name.split("/")[1]
        if dataset not in _ACTIVE_DATASETS:
            continue
        out.append((dataset, read_object(minio, name)))
    return out


def day_items(minio: Minio, trade_date: date) -> list[dict[str, Any]]:
    """Parses and dedups every raw RSS payload landed for the date."""
    seen: dict[str, dict[str, Any]] = {}
    for dataset, raw in _read_raw(minio, trade_date):
        for item in parse_rss(raw, dataset):
            seen.setdefault(item["item_id"], item)
    return sorted(seen.values(), key=lambda i: i["item_id"])


def normalize_from_bronze(minio: Minio, trade_date: date, settings: Settings) -> dict[str, Any]:
    """Reads raw RSS for the date, tags and dedups items, lands them to news_rss/items."""
    registry = Registry(read_registry(settings.postgres_dsn))
    items = tag_items(day_items(minio, trade_date), registry)
    payload = json.dumps(items, ensure_ascii=False).encode("utf-8")
    return land_payloads(
        minio,
        source="news_rss",
        dataset="items",
        trade_date=trade_date,
        source_version="v1",
        ingest_run_id=deterministic_run_id(idempotency_key("news_rss", "items", trade_date, "v1")),
        payloads=[payload],
        record_count=len(items),
        expected_universe=len(items),
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
    )


def main() -> None:
    import sys

    settings = get_settings()
    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    manifest = normalize_from_bronze(client(settings), trade_date, settings)
    print(f"normalized {manifest['record_count']} news items for {trade_date}")


if __name__ == "__main__":
    main()
