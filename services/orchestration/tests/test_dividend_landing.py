"""Unit tests for how a dividend document landing reports to the daily flow."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from pipeline.bronze.dividends import PDF_DATASET

from orchestration.tasks import documents

TD = date(2026, 7, 31)


def _manifests(status: str, notes: str = "1 of 2 announced documents landed") -> list[dict[str, Any]]:
    return [
        {
            "dataset": PDF_DATASET,
            "status": status,
            "notes": notes,
            "ingest_run_id": "RUN1",
        },
        {"dataset": "dividend_attachment_index", "status": "SUCCESS", "notes": notes},
    ]


def _run(monkeypatch: pytest.MonkeyPatch, manifests: list[dict[str, Any]]) -> Any:
    monkeypatch.setattr(documents, "client", lambda settings: None)
    monkeypatch.setattr(documents, "land_attachments", lambda minio, td, *, proxy: manifests)
    return documents.land_dividend_attachments.fn(TD)


def test_a_complete_landing_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every announced document landed, so the phase is a clean success."""
    assert _run(monkeypatch, _manifests("SUCCESS")).status == "SUCCESS"


def test_a_partial_landing_reports_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing document is real, so the day is honest about being incomplete."""
    result = _run(monkeypatch, _manifests("PARTIAL"))
    assert result.status == "PARTIAL"
    assert result.notes == "1 of 2 announced documents landed"


def test_a_total_loss_degrades_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Losing every document must not take the whole daily run down with it."""
    assert _run(monkeypatch, _manifests("FAILED")).status == "DEGRADED"


def test_nothing_announced_is_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most days carry no dividend filing at all and that is not a problem."""
    result = _run(monkeypatch, [])
    assert result.status == "SKIPPED"
    assert result.payload is None
