import json
from datetime import date, datetime, timezone
from typing import Any, cast

import pytest

from pipeline.bronze import ingest, manifest
from pipeline.bronze.feeds import FEEDS


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


def test_unmeasured_coverage_is_null_never_one() -> None:
    """Nobody compared anything, so the manifest must say so instead of claiming a clean day."""
    m = manifest.build_manifest(
        ingest_run_id="RUN1",
        source="idx_summary",
        dataset="daily_trade",
        trade_date=date(2026, 7, 2),
        source_version="v1",
        payloads=[b'{"a":1}'],
        record_count=912,
        requested_at=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc),
    )
    quality = m["quality"]
    assert quality["coverage_ratio"] is None
    assert quality["expected_universe"] is None
    assert quality["observed_universe"] is None
    assert not manifest.coverage_evaluated(m)


def test_fetch_and_land_never_claims_a_universe_it_did_not_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug: a short payload used to land SUCCESS at coverage 1.0 on its own say-so."""
    monkeypatch.setattr(ingest, "_put", lambda *a, **kw: None)
    monkeypatch.setattr(
        ingest, "fetch", lambda url, proxy=None: json.dumps(
            {"recordsTotal": 959, "data": [{"StockCode": "AADI"}] * 912}
        ).encode()
    )

    landed = ingest.fetch_and_land(cast(Any, None), FEEDS["daily_trade"], date(2026, 7, 2))

    assert landed["quality"]["coverage_ratio"] is None
    assert landed["quality"]["expected_universe"] is None
    assert landed["status"] == "PARTIAL"
    assert landed["record_count"] == 912
    assert landed["upstream"]["declared_record_total"] == 959
    assert landed["notes"] == "upstream declared 959 rows, 912 arrived"


def test_a_complete_universe_payload_still_awaits_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a whole payload is unverified until something compares it to the universe."""
    monkeypatch.setattr(ingest, "_put", lambda *a, **kw: None)
    monkeypatch.setattr(
        ingest, "fetch", lambda url, proxy=None: json.dumps(
            {"recordsTotal": 2, "data": [{"StockCode": "AADI"}, {"StockCode": "BBCA"}]}
        ).encode()
    )

    landed = ingest.fetch_and_land(cast(Any, None), FEEDS["daily_trade"], date(2026, 7, 2))

    assert landed["status"] == "PARTIAL"
    assert landed["notes"] == "coverage not evaluated yet"


def test_a_side_feed_lands_clean_without_inventing_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """index_level has no security universe, so it reports nothing rather than 1.0."""
    monkeypatch.setattr(ingest, "_put", lambda *a, **kw: None)
    monkeypatch.setattr(
        ingest, "fetch", lambda url, proxy=None: json.dumps(
            {"recordsTotal": 2, "data": [{"IndexCode": "IHSG"}, {"IndexCode": "LQ45"}]}
        ).encode()
    )

    landed = ingest.fetch_and_land(cast(Any, None), FEEDS["index_level"], date(2026, 7, 2))

    assert landed["status"] == "SUCCESS"
    assert landed["quality"]["coverage_ratio"] is None


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


def test_more_rows_than_the_upstream_declared_is_flagged_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream contradicting itself in either direction has not sent a clean payload."""
    monkeypatch.setattr(ingest, "_put", lambda *a, **kw: None)
    monkeypatch.setattr(
        ingest, "fetch", lambda url, proxy=None: json.dumps(
            {"recordsTotal": 2, "data": [{"IndexCode": c} for c in ("IHSG", "LQ45", "EXTRA")]}
        ).encode()
    )

    landed = ingest.fetch_and_land(cast(Any, None), FEEDS["index_level"], date(2026, 7, 2))

    assert landed["status"] == "PARTIAL"
    assert landed["notes"] == "upstream declared 2 rows, 3 arrived"
