"""Unit tests for the hourly retry of the day's blocked IDX fetches."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from pipeline.bronze.ingest import FetchError

from orchestration import flow_sweep
from orchestration.results import PhaseResult
from orchestration.tasks import sweep

TD = date(2026, 8, 13)


def test_a_clean_day_costs_one_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing outstanding means the sweep stops without fetching anything."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(sweep, "unlanded_eod_feeds", lambda minio, td: [])
    monkeypatch.setattr(
        sweep, "fetch_and_land", lambda *a, **kw: pytest.fail("must not fetch on a clean day")
    )
    assert sweep.resweep_feeds.fn(TD).status == "SKIPPED"


def test_a_recovered_feed_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point is landing a feed the earlier window lost."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(sweep, "unlanded_eod_feeds", lambda minio, td: ["company_profile"])
    monkeypatch.setattr(sweep, "fetch_and_land", lambda *a, **kw: {"status": "SUCCESS"})

    result = sweep.resweep_feeds.fn(TD)

    assert result.status == "SUCCESS"
    assert sweep.recovered_feeds(result) == ["company_profile"]


def test_a_still_blocked_feed_leaves_the_rest_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """One feed still blocked must not stop the next one from being tried."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(
        sweep, "unlanded_eod_feeds", lambda minio, td: ["company_profile", "corporate_actions"]
    )

    def flaky(minio: Any, spec: Any, td: date, *, proxy: str | None = None) -> dict[str, Any]:
        if spec.dataset == "profiles":
            raise FetchError(403, "blocked", via_proxy=True)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(sweep, "fetch_and_land", flaky)

    result = sweep.resweep_feeds.fn(TD)

    assert sweep.recovered_feeds(result) == ["corporate_actions"]
    assert result.payload["still_blocked"] == ["company_profile"]


def test_a_fully_blocked_sweep_is_a_skip_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad window is expected, and the next hour tries again."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(sweep, "unlanded_eod_feeds", lambda minio, td: ["daily_trade"])

    def blocked(*a: Any, **kw: Any) -> dict[str, Any]:
        raise FetchError(403, "blocked", via_proxy=True)

    monkeypatch.setattr(sweep, "fetch_and_land", blocked)
    assert sweep.resweep_feeds.fn(TD).status == "SKIPPED"


def test_already_landed_documents_are_not_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-downloading megabytes we already hold would waste proxy bandwidth."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(sweep, "landed", lambda minio, source, dataset, td: True)
    monkeypatch.setattr(
        sweep, "land_attachments", lambda *a, **kw: pytest.fail("must not refetch")
    )
    assert sweep.resweep_dividend_documents.fn(TD).status == "SKIPPED"


def test_only_a_universe_feed_forces_a_rebuild() -> None:
    """A reference feed is picked up by tomorrow's build, prices are not."""
    assert flow_sweep._rebuild_needed(["daily_trade"])
    assert not flow_sweep._rebuild_needed(["company_profile", "corporate_actions"])
    assert not flow_sweep._rebuild_needed([])


def test_recovered_feeds_tolerates_a_skip() -> None:
    """A skipped sweep carries no payload, and must not blow up the caller."""
    assert sweep.recovered_feeds(PhaseResult(status="SKIPPED")) == []
