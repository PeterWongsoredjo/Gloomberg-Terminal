"""
Pulls the filed source documents that only exist as attachments into Bronze.
"""

from __future__ import annotations

from datetime import date

from prefect import task

from pipeline.bronze.dividend_text import land_filing_text
from pipeline.bronze.dividends import PDF_DATASET, land_attachments
from pipeline.bronze.ingest import client
from pipeline.config import get_settings

from orchestration.results import PhaseResult

# a lost document is worth reporting, never worth failing the day over
_STATUS = {"SUCCESS": "SUCCESS", "PARTIAL": "PARTIAL", "FAILED": "DEGRADED"}


@task(name="land_dividend_attachments", tags=["minio_fetch"])
def land_dividend_attachments(trade_date: date) -> PhaseResult:
    """Lands the announced dividend PDFs, tolerating the ones the proxy loses."""
    settings = get_settings()
    manifests = land_attachments(
        client(settings), trade_date, proxy=settings.webshare_proxy_url
    )
    if not manifests:
        return PhaseResult(status="SKIPPED", notes="no dividend documents announced")

    pdfs = next(m for m in manifests if m["dataset"] == PDF_DATASET)
    return PhaseResult(
        status=_STATUS.get(str(pdfs["status"]), "DEGRADED"),
        payload=manifests,
        notes=str(pdfs["notes"]),
        ingest_run_id=str(pdfs.get("ingest_run_id") or "") or None,
    )


@task(name="extract_dividend_filings", tags=["minio_fetch"])
def extract_dividend_filings(trade_date: date) -> PhaseResult:
    """Reads the landed dividend documents into text the extraction step can use."""
    settings = get_settings()
    manifest = land_filing_text(client(settings), trade_date)
    if manifest is None:
        return PhaseResult(status="SKIPPED", notes="no landed dividend documents")
    return PhaseResult(
        status=_STATUS.get(str(manifest["status"]), "DEGRADED"),
        payload=manifest,
        notes=str(manifest["notes"]),
        ingest_run_id=str(manifest.get("ingest_run_id") or "") or None,
    )
