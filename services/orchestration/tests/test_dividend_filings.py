"""Unit tests for extracting dividend filings and queueing them for the model."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from orchestration import projection
from orchestration.tasks import documents, project

TD = date(2026, 7, 31)


def _text_row(status: str = "EXTRACTED", **extra: Any) -> dict[str, Any]:
    row = {
        "attachment_id": "SMDR:abc123",
        "ticker": "SMDR",
        "title": "Distribution of interim dividend",
        "announced_at": "2026-07-30T17:38:03",
        "filing_number": "SR.26.07.039/CS/SI",
        "filename": "filing.pdf",
        "is_appendix": False,
        "source_url": "https://www.idx.co.id/x/a.pdf",
        "object_key": "corporate_actions/dividend_attachments/part-RUN-0000.pdf.zst",
        "sha256": "deadbeef",
        "status": status,
        "reason": "",
        "page_count": 4,
        "char_count": 4377,
        "text": "Rp2,5 per saham",
    }
    row.update(extra)
    return row


def test_a_readable_day_reports_what_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text landed for every document that carried any, so the phase succeeds."""
    monkeypatch.setattr(documents, "client", lambda settings: None)
    monkeypatch.setattr(
        documents,
        "land_filing_text",
        lambda minio, td: {"status": "SUCCESS", "notes": "2 of 4 landed documents carried text"},
    )
    result = documents.extract_dividend_filings.fn(TD)
    assert result.status == "SUCCESS"
    assert result.notes == "2 of 4 landed documents carried text"


def test_a_day_with_only_scans_degrades_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreadable filings are a real outcome and must not take the daily run down."""
    monkeypatch.setattr(documents, "client", lambda settings: None)
    monkeypatch.setattr(
        documents, "land_filing_text", lambda minio, td: {"status": "FAILED", "notes": "none readable"}
    )
    assert documents.extract_dividend_filings.fn(TD).status == "DEGRADED"


def test_no_landed_documents_is_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most days carry no dividend filing at all and that is not a problem."""
    monkeypatch.setattr(documents, "client", lambda settings: None)
    monkeypatch.setattr(documents, "land_filing_text", lambda minio, td: None)
    assert documents.extract_dividend_filings.fn(TD).status == "SKIPPED"


def test_only_readable_filings_reach_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan has no text to extract from, so queueing it would waste a model call."""
    queued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(project, "client", lambda settings: None)
    monkeypatch.setattr(
        project,
        "filing_texts",
        lambda minio, td: [_text_row(), _text_row(status="EMPTY_TEXT"), _text_row(status="UNREADABLE")],
    )
    def fake_upsert(dsn: str, rows: list[dict[str, Any]], td: Any, run: Any) -> list[str]:
        queued.append(rows)
        return ["SMDR:abc123"]

    monkeypatch.setattr(project, "upsert_filings", fake_upsert)
    monkeypatch.setattr(project, "pending_filing_count", lambda dsn, td: 1)

    result = project.project_dividend_filings.fn(TD)

    assert result.status == "SUCCESS"
    assert [r["status"] for r in queued[0]] == ["EXTRACTED"]


def test_a_day_with_nothing_readable_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no readable filing there is nothing to claim, so the queue is untouched."""
    monkeypatch.setattr(project, "client", lambda settings: None)
    monkeypatch.setattr(project, "filing_texts", lambda minio, td: [_text_row(status="EMPTY_TEXT")])
    monkeypatch.setattr(
        project, "upsert_filings", lambda dsn, rows, td, run: pytest.fail("must not queue")
    )
    assert project.project_dividend_filings.fn(TD).status == "SKIPPED"


def test_the_queue_insert_never_resets_extraction_state() -> None:
    """A filing re-landed by the 7 day window must not be extracted a second time."""
    assert "on conflict (filing_id) do nothing" in projection._INSERT_FILING
    assert "extract_status" not in projection._INSERT_FILING
    assert "extract_attempts" not in projection._INSERT_FILING


def test_filings_outlive_articles() -> None:
    """A dividend can pay months after filing, so its row must survive the news window."""
    assert projection.FILING_KEEP_DAYS == 90
    assert projection.FILING_KEEP_DAYS > 14
