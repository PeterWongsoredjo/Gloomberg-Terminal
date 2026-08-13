"""
Reads the text out of the landed dividend filings and lands it back in Bronze.

Filings come as two documents, a small text one and a large scanned appendix. Only
the text one yields anything, so the scan is recorded as empty rather than dropped.

Landing the text means a better prompt later never has to re-download the pdfs.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone
from typing import Any

from minio import Minio
from pypdf import PdfReader

from pipeline.bronze.dividends import LIST_FEED, read_index
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import client, land_payloads, read_object
from pipeline.bronze.manifest import deterministic_run_id, idempotency_key
from pipeline.config import Settings, get_settings

TEXT_DATASET = "dividend_filing_text"

# below this a filing is a scan, not a document we can read
MIN_TEXT_CHARS = 200


def extract_text(body: bytes) -> tuple[str, int]:
    """The document's text and page count, raising when the bytes are not a pdf."""
    reader = PdfReader(io.BytesIO(body))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip(), len(reader.pages)


def _text_row(
    entry: dict[str, Any],
    status: str,
    *,
    text: str = "",
    page_count: int = 0,
    reason: str = "",
) -> dict[str, Any]:
    """One row saying what reading one landed document produced."""
    return {
        "attachment_id": entry["attachment_id"],
        "ticker": entry["ticker"],
        "title": entry["title"],
        "announced_at": entry["announced_at"],
        "filing_number": entry["filing_number"],
        "filename": entry["filename"],
        "is_appendix": entry["is_appendix"],
        "source_url": entry["source_url"],
        "object_key": entry["object_key"],
        "sha256": entry["sha256"],
        "status": status,
        "reason": reason,
        "page_count": page_count,
        "char_count": len(text),
        "text": text,
    }


def _read_one(minio: Minio, entry: dict[str, Any]) -> dict[str, Any]:
    """Reads one landed document, recording a scan or a bad file as its own outcome."""
    try:
        body = read_object(minio, str(entry["object_key"]))
        text, page_count = extract_text(body)
    except Exception as exc:  # a broken pdf is evidence too, never a crash
        return _text_row(entry, "UNREADABLE", reason=str(exc)[:200])
    if len(text) < MIN_TEXT_CHARS:
        return _text_row(
            entry,
            "EMPTY_TEXT",
            text=text,
            page_count=page_count,
            reason="no extractable text, likely a scan",
        )
    return _text_row(entry, "EXTRACTED", text=text, page_count=page_count)


def filing_texts(minio: Minio, trade_date: date) -> list[dict[str, Any]]:
    """Reads every document the index says landed, in index order."""
    return [_read_one(minio, entry) for entry in read_index(minio, trade_date) if entry["status"] == "LANDED"]


def land_filing_text(minio: Minio, trade_date: date) -> dict[str, Any] | None:
    """Lands the day's filing text as one payload, or nothing when no document landed."""
    rows = filing_texts(minio, trade_date)
    if not rows:
        return None
    spec = FEEDS[LIST_FEED]
    readable = sum(1 for row in rows if row["status"] == "EXTRACTED")
    return land_payloads(
        minio,
        source=spec.source,
        dataset=TEXT_DATASET,
        trade_date=trade_date,
        source_version=spec.source_version,
        ingest_run_id=deterministic_run_id(
            idempotency_key(spec.source, TEXT_DATASET, trade_date, spec.source_version)
        ),
        payloads=[json.dumps(rows, ensure_ascii=False).encode("utf-8")],
        record_count=readable,
        expected_universe=len(rows),
        missing_tickers=[],
        requested_at=datetime.now(timezone.utc),
        status="SUCCESS" if readable else "PARTIAL",
        notes=f"{readable} of {len(rows)} landed documents carried text",
    )


def main() -> None:
    """CLI: read a trade_date's landed dividend filings into Bronze."""
    import sys

    settings: Settings = get_settings()
    trade_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    manifest = land_filing_text(client(settings), trade_date)
    if manifest is None:
        print(f"no landed dividend documents for {trade_date}")
        return
    print(
        f"read {manifest['record_count']} of {manifest['quality']['expected_universe']} "
        f"documents for {trade_date} status={manifest['status']}"
    )


if __name__ == "__main__":
    main()
