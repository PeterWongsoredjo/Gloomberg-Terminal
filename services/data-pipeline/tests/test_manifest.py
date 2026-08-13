from datetime import date, datetime, timezone
from typing import Any, cast

import pytest

from pipeline.bronze import ingest, manifest


def _build(missing: list[str], expected: int) -> dict[str, Any]:
    return manifest.build_manifest(
        ingest_run_id="RUN1",
        source="idx_summary",
        dataset="daily_trade",
        trade_date=date(2026, 7, 2),
        source_version="v1",
        payloads=[b'{"a":1}', b'{"b":2}'],
        record_count=expected - len(missing),
        expected_universe=expected,
        missing_tickers=missing,
        requested_at=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc),
    )


def test_full_coverage_is_success() -> None:
    m = _build(missing=[], expected=2)
    assert m["status"] == "SUCCESS"
    assert m["quality"]["coverage_ratio"] == 1.0


def test_missing_tickers_downgrade_to_partial() -> None:
    m = _build(missing=["XXXX", "YYYY"], expected=5)
    assert m["status"] == "PARTIAL"
    assert m["quality"]["coverage_ratio"] == 0.6
    assert m["quality"]["missing_tickers"] == ["XXXX", "YYYY"]


def test_idempotency_key_is_scope_stable() -> None:
    assert manifest.idempotency_key("idx_summary", "daily_trade", date(2026, 7, 2), "v1") == (
        "idx_summary:daily_trade:2026-07-02:v1"
    )


def test_content_hash_is_order_independent() -> None:
    assert manifest.content_sha256([b"a", b"b"]) == manifest.content_sha256([b"b", b"a"])


def test_timestamps_are_utc_z_suffixed() -> None:
    m = _build(missing=[], expected=2)
    assert m["requested_at"].endswith("Z")
    assert m["completed_at"].endswith("Z")


def test_land_payloads_carries_a_caller_status_and_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that knows the landing was incomplete can say so on the manifest."""
    written: dict[str, Any] = {}

    def fake_put(minio: Any, key: str, data: bytes, content_type: str) -> None:
        written[key] = data

    monkeypatch.setattr(ingest, "_put", fake_put)
    landed = ingest.land_payloads(
        cast(Any, None),
        source="corporate_actions",
        dataset="dividend_attachments",
        trade_date=date(2026, 7, 31),
        source_version="v1",
        ingest_run_id="RUN1",
        payloads=[],
        record_count=0,
        expected_universe=1,
        missing_tickers=["SMDR"],
        requested_at=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
        ext="pdf",
        status="FAILED",
        notes="0 of 2 announced documents landed",
    )
    assert landed["status"] == "FAILED"
    assert landed["notes"] == "0 of 2 announced documents landed"
