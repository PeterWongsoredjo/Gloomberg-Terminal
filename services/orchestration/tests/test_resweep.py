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


def _verdict(promotable: bool, notes: str = "") -> PhaseResult:
    return PhaseResult(
        status="SUCCESS" if promotable else "FAILED", payload=promotable, notes=notes
    )


def _gate(monkeypatch: pytest.MonkeyPatch, promotable: bool, notes: str = "") -> list[Any]:
    """Stands in for the shared gate, recording that the sweep actually consulted it."""
    asked: list[Any] = []

    def gate(manifests: Any, td: date, *a: Any, **kw: Any) -> PhaseResult:
        asked.append(td)
        return _verdict(promotable, notes)

    monkeypatch.setattr(sweep, "run_coverage_gate", gate)
    monkeypatch.setattr(sweep, "universe_manifests", lambda minio, td: [])
    return asked


def _fetches(monkeypatch: pytest.MonkeyPatch, pending: list[str]) -> None:
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(sweep, "unlanded_eod_feeds", lambda minio, td: pending)
    monkeypatch.setattr(sweep, "fetch_and_land", lambda *a, **kw: {"status": "SUCCESS"})


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
    _fetches(monkeypatch, ["company_profile"])
    _gate(monkeypatch, promotable=True)

    result = sweep.resweep_feeds.fn(TD)

    assert result.status == "SUCCESS"
    assert sweep.recovered_feeds(result) == ["company_profile"]


def test_every_recovery_is_put_through_the_shared_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep must not invent its own rules; it asks the gate the daily flow asks."""
    _fetches(monkeypatch, ["daily_trade"])
    asked = _gate(monkeypatch, promotable=True)

    sweep.resweep_feeds.fn(TD)

    assert asked == [TD]


def test_a_still_blocked_feed_leaves_the_rest_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """One feed still blocked must not stop the next one from being tried."""
    monkeypatch.setattr(sweep, "client", lambda settings: None)
    monkeypatch.setattr(
        sweep, "unlanded_eod_feeds", lambda minio, td: ["company_profile", "corporate_actions"]
    )
    _gate(monkeypatch, promotable=True)

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
    monkeypatch.setattr(
        sweep, "run_coverage_gate", lambda *a, **kw: pytest.fail("nothing landed to judge")
    )

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


# ------------------------------------------------- the verdict decides, never the feed name


def test_recovered_prices_alone_never_force_a_promotion() -> None:
    """A recovered universe feed is new bytes, not evidence those bytes are any good."""
    assert not flow_sweep._rebuild_needed(["daily_trade"], False)
    assert flow_sweep._rebuild_needed(["daily_trade"], True)


def test_a_recovered_profile_rebuilds_only_once_the_gate_agrees() -> None:
    """Same-day authority restores a stale day; still-stale authority must not ship it."""
    assert not flow_sweep._rebuild_needed(["company_profile"], False)
    assert flow_sweep._rebuild_needed(["company_profile"], True)


def test_a_recovered_side_feed_follows_the_daily_verdict() -> None:
    """index_level recovering says nothing about whether the day's prices may promote."""
    assert not flow_sweep._rebuild_needed(["index_level"], False)
    assert flow_sweep._rebuild_needed(["index_level"], True)


def test_nothing_recovered_never_rebuilds() -> None:
    """A passing verdict on an unchanged day is not a reason to republish it."""
    assert not flow_sweep._rebuild_needed([], True)
    assert not flow_sweep._rebuild_needed([], False)


def test_a_failed_verdict_reaches_the_flow_as_no_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the sweep task: coverage FAILED must not reach promote_gold."""
    _fetches(monkeypatch, ["daily_trade"])
    _gate(monkeypatch, promotable=False, notes="coverage 0.7294 -> blocked")

    result = sweep.resweep_feeds.fn(TD)

    assert sweep.recovered_feeds(result) == ["daily_trade"]
    assert sweep.coverage_recovered(result) is False
    assert not flow_sweep._rebuild_needed(sweep.recovered_feeds(result), sweep.coverage_recovered(result))


def test_a_promotable_verdict_reaches_the_flow_as_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fetches(monkeypatch, ["daily_trade"])
    _gate(monkeypatch, promotable=True)

    result = sweep.resweep_feeds.fn(TD)

    assert flow_sweep._rebuild_needed(sweep.recovered_feeds(result), sweep.coverage_recovered(result))


def test_a_recovered_profile_that_restores_same_day_authority_promotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale-blocked day's actual recovery path."""
    _fetches(monkeypatch, ["company_profile"])
    _gate(monkeypatch, promotable=True)

    result = sweep.resweep_feeds.fn(TD)

    assert sweep.coverage_recovered(result) is True
    assert flow_sweep._rebuild_needed(sweep.recovered_feeds(result), True)


def test_a_recovered_profile_that_is_still_stale_does_not_promote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fetches(monkeypatch, ["company_profile"])
    _gate(monkeypatch, promotable=False, notes="same-day company-profile authority unavailable")

    result = sweep.resweep_feeds.fn(TD)

    assert sweep.coverage_recovered(result) is False
    assert not flow_sweep._rebuild_needed(sweep.recovered_feeds(result), False)


def test_coverage_recovered_tolerates_a_skip() -> None:
    """A sweep that landed nothing carries no verdict, and must not blow up the caller."""
    assert sweep.coverage_recovered(PhaseResult(status="SKIPPED")) is False


def test_recovered_feeds_tolerates_a_skip() -> None:
    """A skipped sweep carries no payload, and must not blow up the caller."""
    assert sweep.recovered_feeds(PhaseResult(status="SKIPPED")) == []
