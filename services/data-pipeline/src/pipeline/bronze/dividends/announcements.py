from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urljoin

from minio import Minio
from minio.error import S3Error

from pipeline.bronze.core import store
from pipeline.bronze.core.feeds import FEEDS
from pipeline.bronze.core.manifest import deterministic_run_id, idempotency_key
from pipeline.bronze.core.policy import run_id_for
from pipeline.bronze.core.store import read_object

IDX_ORIGIN = "https://www.idx.co.id"
LIST_FEED = "dividend_announcements"
PDF_DATASET = "dividend_attachments"
INDEX_DATASET = "dividend_attachment_index"


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    ticker: str
    title: str
    announced_at: str
    filing_number: str
    filename: str
    is_appendix: bool
    url: str


def _attachment_id(ticker: str, url: str) -> str:
    """A stable id for one document, so a re-run names it the same way."""
    return f"{ticker}:{hashlib.sha1(url.encode()).hexdigest()[:16]}"


def parse_attachments(raw: bytes) -> list[Attachment]:
    """Reads every announced document out of the list payload, raising when the shape drifts."""
    payload = json.loads(raw)
    replies = payload.get("Replies") if isinstance(payload, dict) else None
    if not isinstance(replies, list):
        raise ValueError("announcement payload carries no Replies list")

    out: list[Attachment] = []
    for reply in replies:
        announcement = reply.get("pengumuman") or {}
        # IDX pads the ticker out to a fixed width
        ticker = str(announcement.get("Kode_Emiten") or "").strip()
        title = str(announcement.get("JudulPengumuman") or "").strip()
        announced_at = str(announcement.get("TglPengumuman") or "")
        filing_number = str(announcement.get("NoPengumuman") or "").strip()
        for attachment in reply.get("attachments") or []:
            raw_url = str(attachment.get("FullSavePath") or "").strip()
            if not raw_url:
                continue
            url = urljoin(IDX_ORIGIN, raw_url)
            out.append(
                Attachment(
                    attachment_id=_attachment_id(ticker, url),
                    ticker=ticker,
                    title=title,
                    announced_at=announced_at,
                    filing_number=filing_number,
                    filename=str(attachment.get("OriginalFilename") or "").strip(),
                    is_appendix=bool(attachment.get("IsAttachment")),
                    url=url,
                )
            )
    return out


def read_list_payload(minio: Minio, trade_date: date) -> bytes | None:
    """The day's landed announcement list, or None when stage one never landed it."""
    spec = FEEDS[LIST_FEED]
    key = store.object_key(
        spec.source,
        spec.dataset,
        trade_date,
        spec.source_version,
        run_id_for(spec, trade_date),
        0,
        spec.ext,
    )
    try:
        return read_object(minio, key)
    except S3Error:
        return None


# find pdfs through this index, never by globbing them
def read_index(minio: Minio, trade_date: date) -> list[dict[str, Any]]:
    """The day's attachment index, empty when the pdf stage never ran."""
    spec = FEEDS[LIST_FEED]
    key = store.object_key(
        spec.source,
        INDEX_DATASET,
        trade_date,
        spec.source_version,
        deterministic_run_id(
            idempotency_key(spec.source, INDEX_DATASET, trade_date, spec.source_version)
        ),
        0,
        "json",
    )
    try:
        rows: list[dict[str, Any]] = json.loads(read_object(minio, key))
    except S3Error:
        return []
    return rows
