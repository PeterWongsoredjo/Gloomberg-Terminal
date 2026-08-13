"""
Downloads the PDFs filed with IDX dividend announcements into Bronze.

The announcement list only names the documents, the rupiah per share and the dates
live inside the attached PDFs. This lands those bytes untouched so the extraction
step can read them later.

Read the landed index to find a document. Never glob the pdf dataset directly, a
re-run replaces the objects it lands and can leave an earlier run's tail behind.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin

from minio import Minio
from minio.error import S3Error

from pipeline.bronze import paths
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import (
    FetchError,
    client,
    fetch,
    is_transient_fetch,
    land_payloads,
    read_object,
    run_id_for,
)
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.config import Settings, get_settings

IDX_ORIGIN = "https://www.idx.co.id"
LIST_FEED = "dividend_announcements"
PDF_DATASET = "dividend_attachments"
INDEX_DATASET = "dividend_attachment_index"

# many short retries beat backoff here, each attempt draws a fresh proxy ip
PDF_ATTEMPTS = 8
PDF_RETRY_DELAY_SECONDS = 2.0
MAX_ATTACHMENTS = 60


@dataclass(frozen=True)
class Attachment:
    """One document filed with one announcement, as the list payload described it."""

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
    key = paths.object_key(
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


def _fetch_pdf(url: str, *, proxy: str | None) -> bytes:
    """Retries a blocked download, because the next attempt gets a different ip."""
    for _ in range(PDF_ATTEMPTS - 1):
        try:
            return fetch(url, proxy=proxy)
        except FetchError as exc:
            if not is_transient_fetch(exc):
                raise
            time.sleep(PDF_RETRY_DELAY_SECONDS)
    return fetch(url, proxy=proxy)


def _index_row(
    attachment: Attachment,
    status: str,
    *,
    reason: str = "",
    seq: int | None = None,
    object_key: str = "",
    byte_count: int = 0,
    sha256: str = "",
) -> dict[str, Any]:
    """One index row saying what happened to one announced document."""
    return {
        "attachment_id": attachment.attachment_id,
        "ticker": attachment.ticker,
        "title": attachment.title,
        "announced_at": attachment.announced_at,
        "filing_number": attachment.filing_number,
        "filename": attachment.filename,
        "is_appendix": attachment.is_appendix,
        "source_url": attachment.url,
        "status": status,
        "reason": reason,
        "seq": seq,
        "object_key": object_key,
        "byte_count": byte_count,
        "sha256": sha256,
    }


def _collect(
    attachments: list[Attachment], trade_date: date, run_id: str, proxy: str | None
) -> tuple[list[bytes], list[dict[str, Any]]]:
    """Downloads what it can and records a row for every announced document."""
    spec = FEEDS[LIST_FEED]
    payloads: list[bytes] = []
    rows: list[dict[str, Any]] = []
    for position, attachment in enumerate(attachments):
        if position >= MAX_ATTACHMENTS:
            rows.append(_index_row(attachment, "SKIPPED_CAP", reason="attachment cap reached"))
            continue
        if not attachment.url.lower().endswith(".pdf"):
            rows.append(_index_row(attachment, "SKIPPED_NOT_PDF", reason="not a pdf"))
            continue
        try:
            body = _fetch_pdf(attachment.url, proxy=proxy)
        except FetchError as exc:
            rows.append(_index_row(attachment, "MISSING", reason=str(exc)))
            continue
        seq = len(payloads)
        payloads.append(body)
        rows.append(
            _index_row(
                attachment,
                "LANDED",
                seq=seq,
                object_key=paths.object_key(
                    spec.source, PDF_DATASET, trade_date, spec.source_version, run_id, seq, "pdf"
                ),
                byte_count=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        )
    return payloads, rows


def land_attachments(
    minio: Minio, trade_date: date, *, proxy: str | None = None
) -> list[dict[str, Any]]:
    """Downloads every announced dividend PDF and lands the bytes beside an index."""
    raw = read_list_payload(minio, trade_date)
    if raw is None:
        return []
    attachments = parse_attachments(raw)
    if not attachments:
        return []

    spec = FEEDS[LIST_FEED]
    run_id = deterministic_run_id(
        idempotency_key(spec.source, PDF_DATASET, trade_date, spec.source_version)
    )
    payloads, rows = _collect(attachments, trade_date, run_id, proxy)

    announced = {row["ticker"] for row in rows if row["ticker"]}
    landed = {row["ticker"] for row in rows if row["status"] == "LANDED" and row["ticker"]}
    notes = f"{len(payloads)} of {len(attachments)} announced documents landed"

    pdf_manifest = land_payloads(
        minio,
        source=spec.source,
        dataset=PDF_DATASET,
        trade_date=trade_date,
        source_version=spec.source_version,
        ingest_run_id=run_id,
        payloads=payloads,
        record_count=len(payloads),
        expected_universe=len(announced),
        missing_tickers=sorted(announced - landed),
        requested_at=datetime.now(timezone.utc),
        ext="pdf",
        status=None if payloads else "FAILED",
        notes=notes,
    )
    index_manifest = land_payloads(
        minio,
        source=spec.source,
        dataset=INDEX_DATASET,
        trade_date=trade_date,
        source_version=spec.source_version,
        ingest_run_id=deterministic_run_id(
            idempotency_key(spec.source, INDEX_DATASET, trade_date, spec.source_version)
        ),
        payloads=[json.dumps(rows, ensure_ascii=False).encode("utf-8")],
        record_count=len(rows),
        expected_universe=len(rows),
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
        notes=notes,
    )
    return [pdf_manifest, index_manifest]


def main() -> None:
    """CLI: land a trade_date's announced dividend PDFs into Bronze."""
    import sys

    settings: Settings = get_settings()
    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    manifests = land_attachments(
        client(settings), trade_date, proxy=settings.webshare_proxy_url
    )
    if not manifests:
        print(f"no dividend documents announced for {trade_date}")
        return
    for manifest in manifests:
        print(
            f"landed {manifest['record_count']} into {manifest['dataset']} "
            f"status={manifest['status']} coverage={manifest['quality']['coverage_ratio']}"
        )


if __name__ == "__main__":
    main()
