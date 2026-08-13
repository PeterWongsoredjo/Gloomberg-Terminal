"""Unit tests for landing the PDFs filed with IDX dividend announcements."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from pipeline.bronze import dividends, paths
from pipeline.bronze.dividends import Attachment, land_attachments, parse_attachments
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.ingest import FetchError

TD = date(2026, 7, 31)
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "frozen"
    / "corporate_actions"
    / "dividend_announcements"
    / "ingest_date=2026-07-31"
    / "source_version=v1"
    / "part-0000.json"
)


def _attachment(ticker: str, url: str) -> Attachment:
    return Attachment(
        attachment_id=f"{ticker}:abc",
        ticker=ticker,
        title="Distribution of interim dividend",
        announced_at="2026-07-31T10:20:58",
        filing_number="SR.26.07.041/CS/SI",
        filename="filing.pdf",
        is_appendix=False,
        url=url,
    )


def _capture_landings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replaces the Bronze write with a recorder, returning the calls it saw."""
    calls: list[dict[str, Any]] = []

    def fake_land(minio: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"dataset": kwargs["dataset"], "record_count": kwargs["record_count"]}

    monkeypatch.setattr(dividends, "land_payloads", fake_land)
    return calls


def test_parse_attachments_reads_the_real_payload_shape() -> None:
    """The committed real capture is the contract, so a shape change fails here first."""
    attachments = parse_attachments(FIXTURE.read_bytes())
    assert len(attachments) == 4
    assert {a.ticker for a in attachments} == {"SMDR"}
    assert all(a.url.startswith("https://www.idx.co.id/StaticData/") for a in attachments)
    assert all(a.url.endswith(".pdf") for a in attachments)
    assert [a.is_appendix for a in attachments] == [False, True, False, True]


def test_parse_attachments_strips_the_padded_ticker() -> None:
    """IDX pads Kode_Emiten to a fixed width and an unstripped ticker matches nothing."""
    for attachment in parse_attachments(FIXTURE.read_bytes()):
        assert attachment.ticker == attachment.ticker.strip()
        assert len(attachment.ticker) == 4


def test_parse_attachments_makes_a_relative_path_absolute() -> None:
    """A site-relative attachment path still resolves to a fetchable url."""
    raw = json.dumps(
        {
            "Replies": [
                {
                    "pengumuman": {"Kode_Emiten": "ANTM  ", "JudulPengumuman": "Dividen"},
                    "attachments": [{"FullSavePath": "/StaticData/x/y.pdf"}],
                }
            ]
        }
    ).encode()
    assert parse_attachments(raw)[0].url == "https://www.idx.co.id/StaticData/x/y.pdf"


def test_parse_attachments_raises_when_the_shape_drifts() -> None:
    """A renamed field must fail loudly, never read as no dividends were ever announced."""
    with pytest.raises(ValueError):
        parse_attachments(b'{"data": []}')


def test_parse_attachments_ignores_an_attachment_with_no_path() -> None:
    """An attachment row carrying no url describes no document."""
    raw = json.dumps(
        {"Replies": [{"pengumuman": {"Kode_Emiten": "BBCA"}, "attachments": [{"Id": 1}]}]}
    ).encode()
    assert parse_attachments(raw) == []


def test_attachment_id_is_stable_for_a_url() -> None:
    """The same document keeps its id across runs so the index stays comparable."""
    first = parse_attachments(FIXTURE.read_bytes())[0]
    second = parse_attachments(FIXTURE.read_bytes())[0]
    assert first.attachment_id == second.attachment_id
    assert first.attachment_id.startswith("SMDR:")


def test_land_attachments_indexes_every_pdf_by_its_real_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index must point at the key the pdf actually landed at, not a guessed one."""
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: FIXTURE.read_bytes())
    monkeypatch.setattr(dividends, "_fetch_pdf", lambda url, *, proxy: PDF)
    calls = _capture_landings(monkeypatch)

    land_attachments(cast(Any, None), TD)

    pdf_call = next(c for c in calls if c["dataset"] == dividends.PDF_DATASET)
    index_call = next(c for c in calls if c["dataset"] == dividends.INDEX_DATASET)
    assert pdf_call["ext"] == "pdf"
    assert pdf_call["record_count"] == 4

    rows = json.loads(index_call["payloads"][0])
    spec = FEEDS[dividends.LIST_FEED]
    for row in rows:
        assert row["status"] == "LANDED"
        assert row["object_key"] == paths.object_key(
            spec.source,
            dividends.PDF_DATASET,
            TD,
            spec.source_version,
            pdf_call["ingest_run_id"],
            row["seq"],
            "pdf",
        )


def test_land_attachments_lists_an_issuer_whose_document_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost download must show up as a missing ticker, never as a silent success."""
    monkeypatch.setattr(
        dividends,
        "parse_attachments",
        lambda raw: [
            _attachment("SMDR", "https://x/a.pdf"),
            _attachment("ANTM", "https://x/b.pdf"),
        ],
    )
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: b"{}")

    def flaky(url: str, *, proxy: str | None) -> bytes:
        if url.endswith("b.pdf"):
            raise FetchError(403, "blocked", via_proxy=True)
        return PDF

    monkeypatch.setattr(dividends, "_fetch_pdf", flaky)
    calls = _capture_landings(monkeypatch)

    land_attachments(cast(Any, None), TD)

    pdf_call = next(c for c in calls if c["dataset"] == dividends.PDF_DATASET)
    assert pdf_call["record_count"] == 1
    assert pdf_call["missing_tickers"] == ["ANTM"]
    assert pdf_call["expected_universe"] == 2

    rows = json.loads(next(c for c in calls if c["dataset"] == dividends.INDEX_DATASET)["payloads"][0])
    lost = next(r for r in rows if r["ticker"] == "ANTM")
    assert lost["status"] == "MISSING" and lost["reason"]


def test_land_attachments_marks_a_total_loss_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents existed and none landed, so the manifest says FAILED not PARTIAL."""
    monkeypatch.setattr(
        dividends, "parse_attachments", lambda raw: [_attachment("SMDR", "https://x/a.pdf")]
    )
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: b"{}")

    def always_blocked(url: str, *, proxy: str | None) -> bytes:
        raise FetchError(403, "blocked", via_proxy=True)

    monkeypatch.setattr(dividends, "_fetch_pdf", always_blocked)
    calls = _capture_landings(monkeypatch)

    land_attachments(cast(Any, None), TD)

    pdf_call = next(c for c in calls if c["dataset"] == dividends.PDF_DATASET)
    assert pdf_call["status"] == "FAILED"
    assert pdf_call["payloads"] == []
    rows = json.loads(next(c for c in calls if c["dataset"] == dividends.INDEX_DATASET)["payloads"][0])
    assert [r["status"] for r in rows] == ["MISSING"]


def test_land_attachments_skips_a_date_stage_one_never_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no announcement list there is nothing to claim, so nothing is written."""
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: None)
    calls = _capture_landings(monkeypatch)
    assert land_attachments(cast(Any, None), TD) == []
    assert calls == []


def test_land_attachments_skips_a_day_with_nothing_announced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty Replies list is a real answer and writes no pdf dataset."""
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: b'{"Replies": []}')
    calls = _capture_landings(monkeypatch)
    assert land_attachments(cast(Any, None), TD) == []
    assert calls == []


def test_land_attachments_keeps_a_non_pdf_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spreadsheet attachment is recorded as skipped rather than quietly dropped."""
    monkeypatch.setattr(
        dividends, "parse_attachments", lambda raw: [_attachment("SMDR", "https://x/a.xlsx")]
    )
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: b"{}")
    calls = _capture_landings(monkeypatch)

    land_attachments(cast(Any, None), TD)

    rows = json.loads(next(c for c in calls if c["dataset"] == dividends.INDEX_DATASET)["payloads"][0])
    assert rows[0]["status"] == "SKIPPED_NOT_PDF"


def test_the_work_cap_is_recorded_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documents past the cap stay visible in the index so the gap is never invisible."""
    many = [_attachment("SMDR", f"https://x/{i}.pdf") for i in range(dividends.MAX_ATTACHMENTS + 2)]
    monkeypatch.setattr(dividends, "parse_attachments", lambda raw: many)
    monkeypatch.setattr(dividends, "read_list_payload", lambda minio, td: b"{}")
    monkeypatch.setattr(dividends, "_fetch_pdf", lambda url, *, proxy: PDF)
    calls = _capture_landings(monkeypatch)

    land_attachments(cast(Any, None), TD)

    rows = json.loads(next(c for c in calls if c["dataset"] == dividends.INDEX_DATASET)["payloads"][0])
    assert sum(1 for r in rows if r["status"] == "SKIPPED_CAP") == 2
    assert sum(1 for r in rows if r["status"] == "LANDED") == dividends.MAX_ATTACHMENTS


def test_a_blocked_download_is_retried_until_a_fresh_ip_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy hands out bad ips, so a blocked download deserves another attempt."""
    attempts: list[int] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def flaky(url: str, *, proxy: str | None = None) -> bytes:
        attempts.append(1)
        if len(attempts) < 3:
            raise FetchError(403, "blocked", via_proxy=True)
        return PDF

    monkeypatch.setattr(dividends, "fetch", flaky)
    assert dividends._fetch_pdf("https://x/a.pdf", proxy="http://p") == PDF
    assert len(attempts) == 3


def test_a_real_404_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing document will still be missing on the next attempt."""
    attempts: list[int] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def gone(url: str, *, proxy: str | None = None) -> bytes:
        attempts.append(1)
        raise FetchError(404, "gone")

    monkeypatch.setattr(dividends, "fetch", gone)
    with pytest.raises(FetchError):
        dividends._fetch_pdf("https://x/a.pdf", proxy="http://p")
    assert len(attempts) == 1
