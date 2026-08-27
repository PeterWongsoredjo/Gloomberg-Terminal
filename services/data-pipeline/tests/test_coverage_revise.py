"""Stamping a measured coverage result back onto the run's own manifest."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from pipeline.bronze import ingest, paths
from pipeline.bronze.feeds import FEEDS
from pipeline.bronze.manifest import coverage_evaluated
from pipeline.quality import revise
from pipeline.quality.coverage import CoverageResult

TD = date(2026, 7, 3)
DAILY_TRADE = FEEDS["daily_trade"]


def _result(missing: list[str], expected: int = 4, stale: int = 0) -> CoverageResult:
    observed = expected - len(missing)
    return CoverageResult(
        expected_universe=expected,
        observed_universe=observed,
        missing_tickers=missing,
        coverage_ratio=round(observed / expected, 4),
        as_of=TD,
        stale_days=stale,
        authority_key="company_profile/profiles/ingest_date=2026-07-03/part-x-0000.json.zst",
    )


def _landed(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Lands a daily-trade run the way live ingest does, and captures what it wrote."""
    written: dict[str, bytes] = {}
    monkeypatch.setattr(
        ingest, "_put", lambda minio, key, data, content_type: written.__setitem__(key, data)
    )
    monkeypatch.setattr(
        ingest,
        "fetch",
        lambda url, proxy=None: json.dumps(
            {"recordsTotal": 3, "data": [{"StockCode": t} for t in ("AADI", "BBCA", "TLKM")]}
        ).encode(),
    )
    manifest = ingest.fetch_and_land(None, DAILY_TRADE, TD)  # type: ignore[arg-type]
    return written, manifest


def test_landing_leaves_the_coverage_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _, manifest = _landed(monkeypatch)
    assert not coverage_evaluated(manifest)
    assert manifest["status"] == "PARTIAL"


def test_revision_fills_in_the_measured_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate's answer replaces the open coverage, and nothing else on the manifest."""
    _, landed = _landed(monkeypatch)
    updated = revise.revised(landed, _result(["ERAA"]), "PARTIAL")

    assert updated["quality"]["expected_universe"] == 4
    assert updated["quality"]["observed_universe"] == 3
    assert updated["quality"]["coverage_ratio"] == 0.75
    assert updated["quality"]["missing_tickers"] == ["ERAA"]
    assert updated["status"] == "PARTIAL"
    assert coverage_evaluated(updated)
    assert updated["ingest_run_id"] == landed["ingest_run_id"]
    assert updated["content_sha256"] == landed["content_sha256"]
    assert updated["idempotency_key"] == landed["idempotency_key"]


def test_revision_lands_on_the_same_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """One manifest per run, so dbt, the resweep and telemetry all read one thing."""
    written, landed = _landed(monkeypatch)
    key = paths.manifest_key("idx_summary", "daily_trade", TD, landed["ingest_run_id"])
    assert key in written

    monkeypatch.setattr(
        revise, "read_manifest", lambda minio, source, dataset, td, version: landed
    )
    revise.revise_manifest(None, DAILY_TRADE, TD, _result([]), "SUCCESS")  # type: ignore[arg-type]

    stamped = json.loads(written[key])
    assert stamped["status"] == "SUCCESS"
    assert stamped["quality"]["coverage_ratio"] == 1.0
    assert len(written) == 2  # the payload part plus exactly one manifest


def test_revision_notes_name_the_authority_and_the_gap() -> None:
    notes = revise.coverage_notes(_result(["ERAA", "MSNG"]))
    assert "coverage 2 of 4 expected securities" in notes
    assert "2026-07-03" in notes
    assert "ERAA, MSNG" in notes


def test_revision_notes_call_out_a_stale_universe() -> None:
    stale = CoverageResult(4, 4, [], 1.0, date(2026, 6, 3), 30, "key")
    assert "30d stale" in revise.coverage_notes(stale)


def test_revising_a_run_that_never_landed_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(revise, "read_manifest", lambda *a, **kw: None)
    assert revise.revise_manifest(None, DAILY_TRADE, TD, _result([]), "SUCCESS") is None  # type: ignore[arg-type]


# --------------------------------------- transport completeness is a separate question


def _manifest(record_count: int, declared: int | None, object_count: int = 1) -> dict[str, Any]:
    return {
        "status": "PARTIAL",
        "record_count": record_count,
        "object_count": object_count,
        "upstream": {"declared_record_total": declared},
        "content_sha256": "abc123",
        "idempotency_key": "idx_summary:daily_trade:2026-07-03:v1",
        "notes": "upstream declared 959 rows, 957 arrived",
    }


def test_a_truncated_landing_survives_a_clean_coverage_measurement() -> None:
    """957 of 959 rows arrived, and those 957 happen to be every expected security."""
    updated = revise.revised(_manifest(957, 959), _result([], expected=957), "SUCCESS")

    assert updated["status"] == "PARTIAL"
    assert updated["quality"]["coverage_ratio"] == 1.0
    assert updated["quality"]["missing_tickers"] == []
    assert "upstream declared 959 rows, 957 arrived" in updated["notes"]
    assert "coverage 957 of 957 expected securities" in updated["notes"]


def test_a_clean_landing_with_clean_coverage_is_success() -> None:
    updated = revise.revised(_manifest(959, 959), _result([], expected=957), "SUCCESS")
    assert updated["status"] == "SUCCESS"


def test_the_initial_unmeasured_state_is_not_a_transport_defect() -> None:
    """PARTIAL 'coverage not evaluated yet' carries no numeric contradiction, so it clears."""
    pending = {**_manifest(959, 959), "notes": "coverage not evaluated yet"}
    updated = revise.revised(pending, _result([], expected=957), "SUCCESS")

    assert revise.transport_status(pending) == "SUCCESS"
    assert updated["status"] == "SUCCESS"
    assert "coverage not evaluated yet" not in updated["notes"]


def test_a_truncated_landing_with_failed_coverage_stays_failed() -> None:
    updated = revise.revised(_manifest(700, 959), _result(["AAAA"], expected=957), "FAILED")
    assert updated["status"] == "FAILED"


def test_a_clean_landing_with_failed_coverage_is_failed() -> None:
    updated = revise.revised(_manifest(959, 959), _result(["AAAA"], expected=957), "FAILED")
    assert updated["status"] == "FAILED"


def test_more_rows_than_declared_is_not_clean_either() -> None:
    """An upstream contradicting itself in either direction is still a contradiction."""
    assert revise.transport_status(_manifest(960, 959)) == "PARTIAL"
    updated = revise.revised(_manifest(960, 959), _result([], expected=957), "SUCCESS")
    assert updated["status"] == "PARTIAL"


def test_a_missing_declared_total_cannot_be_judged_on_transport() -> None:
    """No declared total means nothing to compare; the coverage verdict stands alone."""
    assert revise.transport_status(_manifest(957, None)) == "SUCCESS"


def test_revision_never_upgrades_a_fetch_failure() -> None:
    """A landing that wrote no object stays FAILED whatever leftover bytes measured."""
    dead = {**_manifest(0, None, object_count=0), "status": "FAILED"}
    updated = revise.revised(dead, _result([], expected=957), "SUCCESS")

    assert revise.transport_status(dead) == "FAILED"
    assert updated["status"] == "FAILED"


def test_revision_preserves_the_landing_evidence() -> None:
    """The hash, the key and the counts are the audit trail; coverage must not eat them."""
    landed = _manifest(957, 959)
    updated = revise.revised(landed, _result([], expected=957), "SUCCESS")

    for field in ("record_count", "object_count", "content_sha256", "idempotency_key", "upstream"):
        assert updated[field] == landed[field]


def test_the_measured_flags_are_carried_onto_the_manifest() -> None:
    updated = revise.revised(_manifest(959, 959), _result([], expected=957), "FAILED", ["STALE"])
    assert updated["quality"]["dq_flags"] == ["STALE"]
