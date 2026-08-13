"""Unit tests for reading text out of the landed dividend filings."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from pypdf import PdfWriter

from pipeline.bronze import dividend_text
from pipeline.bronze.dividend_text import extract_text, filing_texts, land_filing_text

TD = date(2026, 7, 31)

FILING = Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "smdr_filing_text.pdf"


def _blank_pdf() -> bytes:
    """A valid pdf carrying a page but no text, which is what a scan looks like."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _entry(seq: int, status: str = "LANDED", **extra: Any) -> dict[str, Any]:
    row = {
        "attachment_id": f"SMDR:doc{seq}",
        "ticker": "SMDR",
        "title": "Distribution of interim dividend",
        "announced_at": "2026-07-30T17:38:03",
        "filing_number": "SR.26.07.039/CS/SI",
        "filename": f"doc{seq}.pdf",
        "is_appendix": seq % 2 == 1,
        "source_url": f"https://www.idx.co.id/x/{seq}.pdf",
        "object_key": f"corporate_actions/dividend_attachments/part-RUN-000{seq}.pdf.zst",
        "sha256": "abc",
        "status": status,
        "seq": seq,
    }
    row.update(extra)
    return row


def _capture_landings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_land(minio: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"dataset": kwargs["dataset"], "record_count": kwargs["record_count"]}

    monkeypatch.setattr(dividend_text, "land_payloads", fake_land)
    return calls


def test_a_real_filing_yields_its_text() -> None:
    """The committed real IDX filing parses, so the reader works on the genuine article."""
    text, page_count = extract_text(FILING.read_bytes())
    assert page_count == 4
    assert len(text) > dividend_text.MIN_TEXT_CHARS
    assert "SMDR" in text
    assert "Rp2,5" in text  # the per-share amount the extraction step must find


def test_a_scan_carries_no_text() -> None:
    """An image only pdf parses fine and simply has nothing to read."""
    text, page_count = extract_text(_blank_pdf())
    assert page_count == 1
    assert text == ""


def test_a_scan_is_recorded_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A document we cannot read stays visible in the index as an empty one."""
    monkeypatch.setattr(dividend_text, "read_index", lambda minio, td: [_entry(1)])
    monkeypatch.setattr(dividend_text, "read_object", lambda minio, key: _blank_pdf())

    rows = filing_texts(cast(Any, None), TD)

    assert len(rows) == 1
    assert rows[0]["status"] == "EMPTY_TEXT"
    assert rows[0]["reason"]
    assert rows[0]["char_count"] == 0


def test_a_corrupt_document_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad bytes are evidence of a bad download, not a reason to kill the phase."""
    monkeypatch.setattr(dividend_text, "read_index", lambda minio, td: [_entry(0)])
    monkeypatch.setattr(dividend_text, "read_object", lambda minio, key: b"not a pdf at all")

    rows = filing_texts(cast(Any, None), TD)

    assert rows[0]["status"] == "UNREADABLE"
    assert rows[0]["reason"]


def test_only_landed_documents_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A document the download lost has no bytes, so it is never read or faked."""
    monkeypatch.setattr(
        dividend_text,
        "read_index",
        lambda minio, td: [_entry(0, status="MISSING"), _entry(1, status="SKIPPED_NOT_PDF")],
    )
    monkeypatch.setattr(
        dividend_text, "read_object", lambda minio, key: pytest.fail("must not read a lost document")
    )

    assert filing_texts(cast(Any, None), TD) == []


def test_the_text_lands_as_one_replaceable_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """One payload per date means a re-run replaces it and leaves no tail behind."""
    monkeypatch.setattr(dividend_text, "read_index", lambda minio, td: [_entry(0), _entry(1)])
    monkeypatch.setattr(
        dividend_text,
        "read_object",
        lambda minio, key: FILING.read_bytes() if key.endswith("0000.pdf.zst") else _blank_pdf(),
    )
    calls = _capture_landings(monkeypatch)

    land_filing_text(cast(Any, None), TD)

    assert len(calls) == 1
    call = calls[0]
    assert call["dataset"] == dividend_text.TEXT_DATASET
    assert len(call["payloads"]) == 1
    rows = json.loads(call["payloads"][0])
    assert [r["status"] for r in rows] == ["EXTRACTED", "EMPTY_TEXT"]
    assert call["record_count"] == 1
    assert call["expected_universe"] == 2
    assert call["status"] == "SUCCESS"


def test_a_day_where_nothing_could_be_read_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents landed but none carried text, and the manifest says so."""
    monkeypatch.setattr(dividend_text, "read_index", lambda minio, td: [_entry(1)])
    monkeypatch.setattr(dividend_text, "read_object", lambda minio, key: _blank_pdf())
    calls = _capture_landings(monkeypatch)

    land_filing_text(cast(Any, None), TD)

    assert calls[0]["status"] == "PARTIAL"
    assert calls[0]["record_count"] == 0


def test_a_date_with_no_index_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no landed documents there is nothing to claim, so nothing is written."""
    monkeypatch.setattr(dividend_text, "read_index", lambda minio, td: [])
    calls = _capture_landings(monkeypatch)

    assert land_filing_text(cast(Any, None), TD) is None
    assert calls == []
