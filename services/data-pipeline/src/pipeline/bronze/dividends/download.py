from __future__ import annotations

import hashlib
import time
from datetime import date
from typing import Any

from pipeline.bronze.core import store
from pipeline.bronze.core.feeds import FEEDS
from pipeline.bronze.core.http import fetch, FetchError, is_transient_fetch
from pipeline.bronze.dividends.announcements import LIST_FEED, PDF_DATASET, Attachment

# short retries beat backoff, each attempt gets a fresh ip
PDF_ATTEMPTS = 8
PDF_RETRY_DELAY_SECONDS = 2.0
MAX_ATTACHMENTS = 60


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


def collect_documents(
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
                object_key=store.object_key(
                    spec.source, PDF_DATASET, trade_date, spec.source_version, run_id, seq, "pdf"
                ),
                byte_count=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        )
    return payloads, rows
