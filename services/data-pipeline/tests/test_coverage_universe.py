"""The expected-universe authority and the coverage measurement it feeds."""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pipeline.bronze.feeds import FEEDS
from pipeline.quality import coverage, lifecycle, revise, universe
from tests.fake_bronze import FakeMinio, land_live

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FROZEN = FIXTURES / "frozen"
TD = date(2026, 7, 3)

PROFILE_PART = (
    FROZEN / "company_profile/profiles/ingest_date=2026-07-03/source_version=v1/part-0000.json"
)
TRADE_PART = (
    FROZEN / "idx_summary/daily_trade/ingest_date=2026-07-03/source_version=v1/part-0000.json"
)
ACTIONS_PART = (
    FROZEN / "corporate_actions/issued_history/ingest_date=2026-07-03/source_version=v1/part-0000.json"
)

DAILY_TRADE = FEEDS["daily_trade"]
PROFILES = FEEDS["company_profile"]
ACTIONS = FEEDS["corporate_actions"]

ONLY_TD = frozenset({TD})


def _equity(ticker: str, listed: str = "2020-01-02T00:00:00", **over: Any) -> dict[str, Any]:
    row = {
        "KodeEmiten": ticker,
        "NamaEmiten": f"PT {ticker} Tbk",
        "PapanPencatatan": "Utama",
        "TanggalPencatatan": listed,
        "EfekEmiten_Saham": True,
    }
    row.update(over)
    return row


_UNSET = object()


def _profiles(rows: list[dict[str, Any]], declared: Any = _UNSET) -> bytes:
    total = len(rows) if declared is _UNSET else declared
    return json.dumps({"recordsTotal": total, "data": rows}).encode()


def _trades(tickers: list[str], volume: float = 1000.0) -> bytes:
    rows = [{"StockCode": t, "Volume": volume, "Close": 100.0} for t in tickers]
    return json.dumps({"recordsTotal": len(rows), "data": rows}).encode()


def _universe(tickers: list[str], as_of: date = TD, stale: int = 0) -> universe.ExpectedUniverse:
    return universe.ExpectedUniverse(frozenset(tickers), as_of, stale, "key")


# ---------------------------------------------------------------- pure coverage maths


def test_expected_four_observed_three_is_three_quarters() -> None:
    """The worked example: one absent security costs exactly its share of the universe."""
    result = coverage.evaluate(
        _universe(["AADI", "BBCA", "ERAA", "TLKM"]), {"AADI", "BBCA", "TLKM"}
    )
    assert result.expected_universe == 4
    assert result.observed_universe == 3
    assert result.missing_tickers == ["ERAA"]
    assert result.coverage_ratio == 0.75


def test_extra_tickers_never_inflate_coverage() -> None:
    """Real IDX days carry codes absent from the profile list, and they must not count."""
    result = coverage.evaluate(_universe(["AADI", "BBCA"]), {"AADI", "CNTB", "GOTOM"})
    assert result.observed_universe == 1
    assert result.coverage_ratio == 0.5
    assert result.missing_tickers == ["BBCA"]


def test_a_truncated_payload_cannot_report_full_coverage() -> None:
    """The round-one regression: 912 of the real 959 rows is not a complete day."""
    expected = universe.equity_tickers(
        universe.parse_authority(PROFILE_PART.read_bytes()), TD
    )
    full = json.loads(TRADE_PART.read_text(encoding="utf-8"))
    truncated = json.dumps({**full, "data": full["data"][:912]}).encode()

    clean = coverage.evaluate(
        _universe(sorted(expected)), coverage.observed_tickers(TRADE_PART.read_bytes())
    )
    short = coverage.evaluate(
        _universe(sorted(expected)), coverage.observed_tickers(truncated)
    )

    assert clean.expected_universe == 957 and clean.coverage_ratio == 1.0
    assert short.expected_universe == 957
    assert short.coverage_ratio < 1.0
    assert len(short.missing_tickers) == 47
    assert "UNVR" in short.missing_tickers


def test_zero_volume_active_securities_stay_expected_and_observed() -> None:
    """133 real rows traded nothing that day and every one of them still counts."""
    trade = json.loads(TRADE_PART.read_text(encoding="utf-8"))
    silent = [r["StockCode"] for r in trade["data"] if not r.get("Volume")]
    observed = coverage.observed_tickers(TRADE_PART.read_bytes())
    expected = universe.equity_tickers(
        universe.parse_authority(PROFILE_PART.read_bytes()), TD
    )

    assert len(silent) == 133
    assert set(silent) <= observed
    result = coverage.evaluate(_universe(sorted(expected)), observed)
    assert result.coverage_ratio == 1.0
    assert not result.missing_tickers


def test_watchlist_securities_stay_expected() -> None:
    """A Pemantauan Khusus listing is a real security, not an optional one."""
    profiles = json.loads(PROFILE_PART.read_text(encoding="utf-8"))
    watchlist = {
        r["KodeEmiten"]
        for r in profiles["data"]
        if r.get("PapanPencatatan") == "Pemantauan Khusus"
    }
    expected = universe.equity_tickers(profiles, TD)
    observed = coverage.observed_tickers(TRADE_PART.read_bytes())

    assert len(watchlist) == 156
    assert watchlist <= expected
    assert watchlist <= observed


def test_a_missing_watchlist_security_is_named() -> None:
    """Dropping a watchlist ticker must show up by name, not vanish quietly."""
    result = coverage.evaluate(_universe(["AADI", "MTRA"]), {"AADI"})
    assert result.missing_tickers == ["MTRA"]


# ------------------------------------------------------- declared totals, fail closed


def test_a_payload_with_no_declared_total_fails_closed() -> None:
    """Without the upstream's own count there is nothing to check completeness against."""
    with pytest.raises(universe.UniverseUnavailable, match="declares no recordsTotal"):
        universe.parse_authority(json.dumps({"data": [_equity("AADI")]}).encode())


def test_a_non_integer_declared_total_fails_closed() -> None:
    for bad in ("957", 957.0, None, True):
        with pytest.raises(universe.UniverseUnavailable, match="not an integer"):
            universe.parse_authority(_profiles([_equity("AADI")], declared=bad))


def test_a_declared_total_that_disagrees_either_way_fails_closed() -> None:
    """Fewer rows than declared is truncation; more is a payload nobody understands."""
    rows = [_equity("AADI"), _equity("BBCA")]
    for declared in (3, 1):
        with pytest.raises(universe.UniverseUnavailable, match="disagrees with its own total"):
            universe.parse_authority(_profiles(rows, declared=declared))


def test_the_plausibility_floor_is_not_a_completeness_check() -> None:
    """912 real equities clear the 500 floor, and are still refused with no declared total."""
    real = json.loads(PROFILE_PART.read_text(encoding="utf-8"))["data"][:912]
    with pytest.raises(universe.UniverseUnavailable, match="declares no recordsTotal"):
        universe.parse_authority(json.dumps({"data": real}).encode())


def test_a_matching_declared_total_is_accepted() -> None:
    assert len(universe.parse_authority(PROFILE_PART.read_bytes())["data"]) == 957


# ------------------------------------------- the landing path: capture date, not trade date


def test_a_daily_run_files_the_profile_under_the_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal path is unchanged: capture day and trade date are the same day."""
    fake = FakeMinio()
    landed = land_live(monkeypatch, fake, PROFILES, TD, _profiles([_equity("AADI")]), captured_on=TD)
    assert landed["trade_date"] == TD.isoformat()
    assert all("ingest_date=2026-07-03" in k for k in fake.objects)


def test_a_backfill_cannot_file_todays_profile_under_an_old_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint takes no date, so its answer can only ever be filed under today."""
    fake = FakeMinio()
    old, today = date(2026, 1, 5), date(2026, 8, 27)

    landed = land_live(
        monkeypatch, fake, PROFILES, old, _profiles([_equity("AADI")]), captured_on=today
    )

    assert landed["trade_date"] == today.isoformat()
    assert not [k for k in fake.objects if "ingest_date=2026-01-05" in k]
    assert [k for k in fake.objects if "ingest_date=2026-08-27" in k]


def test_a_backfill_leaves_a_genuine_old_snapshot_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History is evidence, and a rerun must not overwrite it with today's answer."""
    fake = FakeMinio()
    old, today = date(2026, 1, 5), date(2026, 8, 27)
    land_live(monkeypatch, fake, PROFILES, old, _profiles([_equity("GONE")]), captured_on=old)
    genuine = dict(fake.objects)

    land_live(monkeypatch, fake, PROFILES, old, _profiles([_equity("AADI")]), captured_on=today)

    for key, blob in genuine.items():
        assert fake.objects[key] == blob


def test_a_date_scoped_feed_still_files_under_the_date_it_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daily_trade carries the date in its url, so a backfill really is that day's data."""
    fake = FakeMinio()
    landed = land_live(
        monkeypatch, fake, DAILY_TRADE, date(2026, 1, 5), _trades(["AADI"]),
        captured_on=date(2026, 8, 27),
    )
    assert landed["trade_date"] == "2026-01-05"


def test_a_historical_date_with_no_genuine_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rerun of a day nobody captured has no universe, and invents none."""
    fake = FakeMinio()
    old, today = date(2026, 1, 5), date(2026, 8, 27)
    land_live(monkeypatch, fake, PROFILES, old, _profiles([_equity("AADI")]), captured_on=today)

    with pytest.raises(universe.UniverseUnavailable, match="no company_profile snapshot"):
        universe.expected_universe(
            fake, old, allowed_as_of=frozenset({old}), min_securities=1
        )


def test_a_snapshot_filed_under_a_day_it_was_not_taken_on_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This catches anything landed by the old scheme, where the label was the trade date."""
    fake = FakeMinio()
    land_live(monkeypatch, fake, PROFILES, TD, _profiles([_equity("AADI")]), captured_on=TD)
    key = next(k for k in fake.objects if k.startswith("_manifests/"))
    manifest = json.loads(fake.objects[key])
    manifest["requested_at"] = "2026-08-27T05:00:00Z"
    fake.objects[key] = json.dumps(manifest).encode()

    with pytest.raises(universe.UniverseUnavailable, match="was captured 2026-08-27"):
        universe.expected_universe(fake, TD, allowed_as_of=ONLY_TD, min_securities=1)


def test_a_snapshot_with_no_manifest_is_refused() -> None:
    """An object nobody dated is not evidence of any day."""
    fake = FakeMinio()
    fake.objects[
        "company_profile/profiles/ingest_date=2026-07-03/source_version=v1/part-x-0000.json.zst"
    ] = b"unread"
    with pytest.raises(universe.UniverseUnavailable, match="no manifest to date it"):
        universe.expected_universe(fake, TD, allowed_as_of=ONLY_TD, min_securities=1)


# ----------------------------------------------------------------- the allowed window


def test_the_previous_trading_session_is_valid_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Friday's capture still answers for Monday, and says it is a session stale."""
    fake = FakeMinio()
    friday, monday = date(2026, 7, 3), date(2026, 7, 6)
    land_live(
        monkeypatch, fake, PROFILES, friday, _profiles([_equity("AADI"), _equity("BBCA")]),
        captured_on=friday,
    )

    result = universe.expected_universe(
        fake, monday, allowed_as_of=frozenset({monday, friday}), min_securities=1
    )

    assert result.as_of == friday
    assert result.stale_days == 3
    assert result.tickers == frozenset({"AADI", "BBCA"})


def test_a_same_day_capture_wins_over_the_previous_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMinio()
    friday, monday = date(2026, 7, 3), date(2026, 7, 6)
    land_live(monkeypatch, fake, PROFILES, friday, _profiles([_equity("AADI")]), captured_on=friday)
    land_live(
        monkeypatch, fake, PROFILES, monday, _profiles([_equity("AADI"), _equity("NEWO")]),
        captured_on=monday,
    )

    result = universe.expected_universe(
        fake, monday, allowed_as_of=frozenset({monday, friday}), min_securities=1
    )

    assert result.as_of == monday
    assert result.stale_days == 0


def test_an_authority_older_than_the_window_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Thursday capture is two sessions back, so Monday refuses it outright."""
    fake = FakeMinio()
    thursday, friday, monday = date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 6)
    land_live(
        monkeypatch, fake, PROFILES, thursday, _profiles([_equity("AADI")]), captured_on=thursday
    )

    with pytest.raises(universe.UniverseUnavailable, match="no company_profile snapshot"):
        universe.expected_universe(
            fake, monday, allowed_as_of=frozenset({monday, friday}), min_securities=1
        )


def test_an_implausibly_small_universe_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMinio()
    land_live(monkeypatch, fake, PROFILES, TD, _profiles([_equity("AADI")]), captured_on=TD)
    with pytest.raises(universe.UniverseUnavailable, match="plausible floor"):
        universe.expected_universe(fake, TD, allowed_as_of=ONLY_TD, min_securities=500)


# ------------------------------------------------------------------ listing dates


def test_a_new_listing_is_not_expected_before_its_listing_date() -> None:
    """A security that lists tomorrow cannot be missing from today."""
    rows = [_equity("AADI"), _equity("NEWO", listed="2026-07-04T00:00:00")]
    assert universe.equity_tickers({"data": rows}, TD) == {"AADI"}
    assert universe.equity_tickers({"data": rows}, date(2026, 7, 4)) == {"AADI", "NEWO"}


def test_an_unknown_listing_date_stays_expected() -> None:
    """A blank listing date is not evidence of a future listing, so it is not coerced."""
    rows = [_equity("AADI", listed=""), _equity("BBCA", listed="not-a-date")]
    assert universe.equity_tickers({"data": rows}, TD) == {"AADI", "BBCA"}


def test_snapshot_membership_alone_decides_who_is_in_the_snapshot() -> None:
    """What a snapshot proves is membership at capture time, and nothing more than that."""
    rows = [_equity("AADI"), _equity("BBCA")]
    assert universe.equity_tickers({"data": rows}, TD) == {"AADI", "BBCA"}
    assert universe.equity_tickers({"data": [_equity("AADI")]}, TD) == {"AADI"}


# --------------------------------------------------------------- lifecycle events


def _event(ticker: str, when: str, action: str) -> dict[str, Any]:
    return {"KodeEmiten": ticker, "TanggalPencatatan": when, "JenisTindakan": action}


def _events(rows: list[dict[str, Any]]) -> list[lifecycle.LifecycleEvent]:
    return lifecycle.parse_events(json.dumps({"data": rows}).encode())


def test_a_delisting_is_applied_only_after_its_effective_date() -> None:
    """The real IDX delist action carries the date it took effect, and that date rules."""
    events = _events([_event("MFIN", "2026-06-22T00:00:00", "delist")])
    snapshot = _universe(["AADI", "MFIN"], as_of=date(2026, 6, 19))

    before = coverage.apply_lifecycle(snapshot, events, date(2026, 6, 19))
    on_the_day = coverage.apply_lifecycle(snapshot, events, date(2026, 6, 22))
    after = coverage.apply_lifecycle(snapshot, events, date(2026, 6, 23))

    assert "MFIN" in before.tickers
    assert "MFIN" not in on_the_day.tickers
    assert "MFIN" not in after.tickers


def test_a_partial_delisting_never_removes_a_security() -> None:
    """MEGA kept 23 billion shares after its partialDelisting; it still trades."""
    events = _events([_event("MEGA", "2026-05-04T00:00:00", "partialDelisting")])
    kept = coverage.apply_lifecycle(_universe(["MEGA"]), events, TD)
    assert kept.tickers == frozenset({"MEGA"})


def test_a_listing_in_the_gap_is_added_so_it_cannot_hide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale authority plus a truncated trade payload must still name the new listing."""
    friday, monday = date(2026, 7, 3), date(2026, 7, 6)
    snapshot = _universe(["AADI", "BBCA"], as_of=friday, stale=3)
    events = _events([_event("NEWO", "2026-07-06T00:00:00", "ipo")])

    expected = coverage.apply_lifecycle(snapshot, events, monday)
    result = coverage.evaluate(expected, {"AADI", "BBCA"})

    assert "NEWO" in expected.tickers
    assert result.missing_tickers == ["NEWO"]
    assert result.coverage_ratio < 1.0


def test_a_listing_before_the_snapshot_is_not_added_twice() -> None:
    """The snapshot already knows about it, so the event window starts after capture."""
    events = _events([_event("AADI", "2024-12-05T00:00:00", "ipo")])
    assert lifecycle.listed_between(events, date(2026, 7, 3), date(2026, 7, 6)) == set()


def test_the_real_action_feed_agrees_with_the_real_profile_list() -> None:
    """Every ticker the feed says is delisted is absent from the profile list, all 59."""
    events = lifecycle.parse_events(ACTIONS_PART.read_bytes())
    delisted = lifecycle.delisted_through(events, TD)
    profile = universe.equity_tickers(
        universe.parse_authority(PROFILE_PART.read_bytes()), TD
    )
    assert len(delisted) == 59
    assert delisted & profile == set()


def test_undated_and_malformed_events_are_dropped_not_guessed() -> None:
    events = _events(
        [
            _event("AADI", "", "delist"),
            _event("BB", "2026-01-02T00:00:00", "delist"),
            _event("BBCA", "2026-01-02T00:00:00", "delist"),
        ]
    )
    assert [e.ticker for e in events] == ["BBCA"]


# ------------------------------------------------------------ end to end over the fake


def test_measure_reads_the_landed_payloads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authority and observation both come out of Bronze, nothing is declared."""
    fake = FakeMinio()
    land_live(
        monkeypatch, fake, PROFILES, TD,
        _profiles([_equity(t) for t in ("AADI", "BBCA", "ERAA", "TLKM")]), captured_on=TD,
    )
    land_live(monkeypatch, fake, DAILY_TRADE, TD, _trades(["AADI", "BBCA", "TLKM"]), captured_on=TD)

    result = coverage.measure(
        fake, DAILY_TRADE, TD, allowed_as_of=ONLY_TD, min_securities=1
    )

    assert (result.expected_universe, result.observed_universe) == (4, 3)
    assert result.missing_tickers == ["ERAA"]
    assert result.coverage_ratio == 0.75


def test_measure_applies_a_delisting_from_the_landed_action_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A security delisted before the date is not expected, so it is not counted missing."""
    fake = FakeMinio()
    land_live(
        monkeypatch, fake, PROFILES, TD,
        _profiles([_equity(t) for t in ("AADI", "BBCA", "MFIN")]), captured_on=TD,
    )
    land_live(monkeypatch, fake, DAILY_TRADE, TD, _trades(["AADI", "BBCA"]), captured_on=TD)
    land_live(
        monkeypatch, fake, ACTIONS, TD,
        json.dumps({"data": [_event("MFIN", "2026-06-22T00:00:00", "delist")]}).encode(),
        captured_on=TD,
    )

    result = coverage.measure(fake, DAILY_TRADE, TD, allowed_as_of=ONLY_TD, min_securities=1)

    assert result.expected_universe == 2
    assert result.coverage_ratio == 1.0
    assert result.missing_tickers == []


def test_measure_fails_closed_when_nothing_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day whose trade payload never landed has no coverage, not full coverage."""
    fake = FakeMinio()
    land_live(
        monkeypatch, fake, PROFILES, TD,
        _profiles([_equity("AADI"), _equity("BBCA")]), captured_on=TD,
    )
    with pytest.raises(universe.UniverseUnavailable, match="no landed daily_trade"):
        coverage.measure(fake, DAILY_TRADE, TD, allowed_as_of=ONLY_TD, min_securities=1)


def test_bronze_landing_has_no_registry_or_database_dependency() -> None:
    """Raw landing holds the upstream bytes and nothing that needs looking anything up."""
    source = (
        Path(universe.__file__).resolve().parents[1] / "bronze" / "ingest.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = ("psycopg", "duckdb", "pipeline.reference", "pipeline.quality", "pipeline.gold")
    assert not [m for m in imported if m.startswith(forbidden)]


# ------------------------------------------------- the stale-authority policy, end to end


def _stale_monday(monkeypatch: pytest.MonkeyPatch, events: bytes | None = None) -> FakeMinio:
    """Friday's profile is the only authority; Monday's trade payload is short a listing."""
    fake = FakeMinio()
    friday, monday = date(2026, 7, 3), date(2026, 7, 6)
    land_live(
        monkeypatch, fake, PROFILES, friday, _profiles([_equity("AADI")]), captured_on=friday
    )
    land_live(monkeypatch, fake, DAILY_TRADE, monday, _trades(["AADI"]), captured_on=monday)
    if events is not None:
        land_live(monkeypatch, fake, ACTIONS, monday, events, captured_on=monday)
    return fake


def test_a_stale_authority_still_measures_a_full_looking_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the hole: the numbers look perfect because the wrong universe was asked."""
    fake = _stale_monday(monkeypatch)
    monday, friday = date(2026, 7, 6), date(2026, 7, 3)

    result = coverage.measure(
        fake, DAILY_TRADE, monday,
        allowed_as_of=frozenset({monday, friday}), min_securities=1,
    )

    assert result.coverage_ratio == 1.0
    assert result.missing_tickers == []
    assert "NEWO" not in {t for t in result.missing_tickers}
    # the only thing that says the day is untrustworthy is the staleness itself
    assert result.stale_days == 3
    assert result.as_of == friday


def test_a_listing_event_cannot_launder_a_stale_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the feed does know about NEWO, the authority is still yesterday's."""
    events = json.dumps(
        {"data": [_event("NEWO", "2026-07-06T00:00:00", "ipo")]}
    ).encode()
    fake = _stale_monday(monkeypatch, events)
    monday, friday = date(2026, 7, 6), date(2026, 7, 3)

    result = coverage.measure(
        fake, DAILY_TRADE, monday,
        allowed_as_of=frozenset({monday, friday}), min_securities=1,
    )

    assert "NEWO" in result.missing_tickers  # the event did enrich the expected set
    assert result.stale_days == 3  # and the authority is still stale, which is what blocks


def test_same_day_authority_reports_no_staleness(monkeypatch: pytest.MonkeyPatch) -> None:
    """A good day is unaffected by the policy: nothing to block on."""
    fake = FakeMinio()
    monday = date(2026, 7, 6)
    land_live(
        monkeypatch, fake, PROFILES, monday, _profiles([_equity("AADI")]), captured_on=monday
    )
    land_live(monkeypatch, fake, DAILY_TRADE, monday, _trades(["AADI"]), captured_on=monday)

    result = coverage.measure(
        fake, DAILY_TRADE, monday,
        allowed_as_of=frozenset({monday, date(2026, 7, 3)}), min_securities=1,
    )

    assert result.stale_days == 0
    assert result.coverage_ratio == 1.0


# ------------------------- transport completeness and universe completeness are separate


def test_a_transport_truncated_day_can_still_be_universe_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real 959-row day minus its two non-profile codes: 957 rows, every security."""
    full = json.loads(TRADE_PART.read_text(encoding="utf-8"))
    profile = universe.equity_tickers(universe.parse_authority(PROFILE_PART.read_bytes()), TD)
    extras = coverage.observed_tickers(TRADE_PART.read_bytes()) - profile
    trimmed = json.dumps(
        {**full, "data": [r for r in full["data"]
                          if str(r["StockCode"]).strip().upper() not in extras]}
    ).encode()

    fake = FakeMinio()
    land_live(
        monkeypatch, fake, PROFILES, TD,
        json.dumps({"recordsTotal": 957, "data": [
            r for r in json.loads(PROFILE_PART.read_text(encoding="utf-8"))["data"]
        ]}).encode(),
        captured_on=TD,
    )
    landed = land_live(monkeypatch, fake, DAILY_TRADE, TD, trimmed, captured_on=TD)
    result = coverage.measure(fake, DAILY_TRADE, TD, allowed_as_of=ONLY_TD, min_securities=1)
    final = revise.revised(landed, result, "SUCCESS")

    assert sorted(extras) == ["CNTB", "GOTOM"]
    assert landed["record_count"] == 957
    assert landed["upstream"]["declared_record_total"] == 959
    assert result.expected_universe == 957
    assert result.observed_universe == 957
    assert result.coverage_ratio == 1.0
    assert result.missing_tickers == []
    # universe-complete, transport-incomplete: degraded but promotable, never SUCCESS
    assert final["status"] == "PARTIAL"
    assert "upstream declared 959 rows, 957 arrived" in final["notes"]
    assert "coverage 957 of 957 expected securities" in final["notes"]
