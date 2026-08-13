"""Unit tests for spotting the feeds a day never managed to land."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, cast

import pytest

from pipeline.bronze import sweep
from pipeline.bronze.feeds import EOD_FEEDS, FEEDS
from pipeline.bronze.sweep import landed, unlanded_eod_feeds

TD = date(2026, 8, 13)


def _manifests(monkeypatch: pytest.MonkeyPatch, by_dataset: dict[str, str | None]) -> None:
    """Fakes the stored manifest for each dataset, None meaning it never ran."""

    def fake_read(
        minio: Any, source: str, dataset: str, trade_date: date, source_version: str = "v1"
    ) -> dict[str, Any] | None:
        status = by_dataset.get(dataset, "SUCCESS")
        return None if status is None else {"status": status}

    monkeypatch.setattr(sweep, "read_manifest", fake_read)


def test_a_clean_day_has_nothing_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every feed landed, so the sweep must not refetch anything."""
    _manifests(monkeypatch, {})
    assert unlanded_eod_feeds(cast(Any, None), TD) == []


def test_a_blocked_feed_is_worth_another_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAILED manifest is exactly the case an hour later can fix."""
    _manifests(monkeypatch, {"profiles": "FAILED"})
    assert unlanded_eod_feeds(cast(Any, None), TD) == ["company_profile"]


def test_a_feed_that_never_ran_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """No manifest at all means the fetch never happened, not that it succeeded."""
    _manifests(monkeypatch, {"issued_history": None})
    assert unlanded_eod_feeds(cast(Any, None), TD) == ["corporate_actions"]


def test_a_partial_landing_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial coverage means tickers are missing, so another attempt is worth it."""
    _manifests(monkeypatch, {"daily_trade": "PARTIAL"})
    assert unlanded_eod_feeds(cast(Any, None), TD) == ["daily_trade"]


def test_the_sweep_never_looks_at_news(monkeypatch: pytest.MonkeyPatch) -> None:
    """News accumulates under a fresh id each poll, so a stored manifest cannot be found."""
    _manifests(monkeypatch, {})
    assert not any(FEEDS[name].accumulates for name in EOD_FEEDS)
    assert "news_cnbc" not in unlanded_eod_feeds(cast(Any, None), TD)


def test_landed_reads_the_status_of_any_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pdf landing is not a feed, and still needs the same question answered."""
    _manifests(monkeypatch, {"dividend_attachments": "FAILED"})
    assert not landed(cast(Any, None), "corporate_actions", "dividend_attachments", TD)
    assert landed(cast(Any, None), "corporate_actions", "dividend_announcements", TD)


def test_a_missing_manifest_object_reads_as_never_run() -> None:
    """An object store that has no such key means no landing, never an error."""
    from minio.error import S3Error

    def raise_missing(bucket: str, key: str) -> Any:
        raise S3Error(
            code="NoSuchKey",
            message="missing",
            resource=key,
            request_id="req",
            host_id="host",
            response=cast(Any, None),
        )

    minio = type("FakeMinio", (), {"get_object": staticmethod(raise_missing)})()
    assert sweep.read_manifest(cast(Any, minio), "idx_summary", "daily_trade", TD) is None


def test_a_stored_manifest_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real read path returns the manifest body it finds."""

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps({"status": "SUCCESS", "record_count": 963}).encode()

        def close(self) -> None: ...

        def release_conn(self) -> None: ...

    minio = type("FakeMinio", (), {"get_object": staticmethod(lambda b, k: FakeResponse())})()
    manifest = sweep.read_manifest(cast(Any, minio), "idx_summary", "daily_trade", TD)
    assert manifest is not None and manifest["record_count"] == 963
