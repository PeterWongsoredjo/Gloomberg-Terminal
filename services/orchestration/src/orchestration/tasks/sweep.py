"""
Re-attempts the day's blocked feeds, an hour after the last try.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from prefect import task

from pipeline.bronze.dividends import PDF_DATASET, land_attachments
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import FetchError, client, fetch_and_land
from pipeline.bronze.sweep import landed, unlanded_eod_feeds
from pipeline.config import get_settings

from orchestration.results import PhaseResult


@task(name="resweep_feeds", tags=["minio_fetch"])
def resweep_feeds(trade_date: date) -> PhaseResult:
    """Fetches the day's unlanded feeds again, reporting which ones finally came through."""
    settings = get_settings()
    minio = client(settings)
    pending = unlanded_eod_feeds(minio, trade_date)
    if not pending:
        return PhaseResult(status="SKIPPED", notes="every feed already landed")

    recovered: list[str] = []
    for name in pending:
        try:
            fetch_and_land(minio, FEEDS[name], trade_date, proxy=settings.webshare_proxy_url)
            recovered.append(name)
        except FetchError:
            continue  # still blocked, the next sweep tries again

    return PhaseResult(
        status="SUCCESS" if recovered else "SKIPPED",
        payload={"recovered": recovered, "still_blocked": [n for n in pending if n not in recovered]},
        notes=f"{len(recovered)} of {len(pending)} blocked feeds recovered",
    )


@task(name="resweep_dividend_documents", tags=["minio_fetch"])
def resweep_dividend_documents(trade_date: date) -> PhaseResult:
    """Downloads the announced dividend PDFs again when the last attempt lost them."""
    settings = get_settings()
    minio = client(settings)
    if landed(minio, "corporate_actions", PDF_DATASET, trade_date):
        return PhaseResult(status="SKIPPED", notes="dividend documents already landed")

    manifests = land_attachments(minio, trade_date, proxy=settings.webshare_proxy_url)
    if not manifests:
        return PhaseResult(status="SKIPPED", notes="no dividend documents announced")
    pdfs = next(m for m in manifests if m["dataset"] == PDF_DATASET)
    return PhaseResult(
        status="SUCCESS" if pdfs["status"] != "FAILED" else "SKIPPED",
        payload=manifests,
        notes=str(pdfs["notes"]),
    )


def recovered_feeds(result: PhaseResult) -> list[str]:
    """The feed names a sweep managed to land, empty when it landed none."""
    payload: dict[str, Any] = result.payload if isinstance(result.payload, dict) else {}
    return list(payload.get("recovered") or [])
