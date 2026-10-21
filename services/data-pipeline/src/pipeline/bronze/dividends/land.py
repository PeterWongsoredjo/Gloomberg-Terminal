from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from minio import Minio

from pipeline.bronze.core.feeds import FEEDS
from pipeline.bronze.core.land import land_payloads
from pipeline.bronze.core.manifest import deterministic_run_id, idempotency_key
from pipeline.bronze.core.store import client
from pipeline.bronze.dividends.announcements import (
    INDEX_DATASET,
    LIST_FEED,
    PDF_DATASET,
    parse_attachments,
    read_list_payload,
)
from pipeline.bronze.dividends.download import collect_documents
from pipeline.config import Settings, get_settings


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
    payloads, rows = collect_documents(attachments, trade_date, run_id, proxy)

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
