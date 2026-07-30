"""Unit tests for the IDX security registry and its alias generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pipeline.reference import refresh
from pipeline.reference.aliases import aliases_for, generic_words, normalize
from pipeline.reference.refresh import MIN_PLAUSIBLE_SECURITIES
from pipeline.reference.securities import (
    alias_index,
    from_csv,
    load_baseline,
    securities_from_profiles,
    to_csv,
)

if TYPE_CHECKING:
    from minio import Minio

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/frozen/company_profile/profiles/ingest_date=2026-07-03/source_version=v1/part-0000.json"
)


def _fixture_payload() -> object:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_baseline_covers_the_whole_board_plus_the_index() -> None:
    """The committed floor carries every listed equity and IHSG."""
    securities = load_baseline()
    assert len(securities) > MIN_PLAUSIBLE_SECURITIES
    tickers = {s.ticker for s in securities}
    assert "IHSG" in tickers
    assert {"BBCA", "BBRI", "ANTM", "KRAS"} <= tickers
    assert all(len(s.ticker) == 4 and s.ticker.isupper() for s in securities)


def test_aliases_keep_the_name_and_drop_the_bare_category() -> None:
    """A headline saying 'Bank Central Asia' resolves, one saying 'Bank' does not."""
    aliases = aliases_for("PT Bank Central Asia Tbk.")
    assert "BANK CENTRAL ASIA" in aliases
    assert "CENTRAL ASIA" in aliases
    assert "BANK" not in aliases


def test_a_lone_word_alias_is_never_made_by_truncation() -> None:
    """'Bank Permata' must not answer to 'Permata', a common Indonesian noun."""
    assert "PERMATA" not in aliases_for("Bank Permata Tbk")
    assert "FINANCE" not in aliases_for("Buana Finance Tbk")
    assert "RESOURCES" not in aliases_for("Bumi Resources Tbk")
    assert "ELNUSA" in aliases_for("Elnusa Tbk")
    assert "ANTAM" in aliases_for("ANTAM (Persero) Tbk")


def test_a_category_lead_gets_no_two_word_short_form() -> None:
    """'Asuransi Jiwa' names an industry, not an issuer, so it never becomes an alias."""
    aliases = aliases_for("PT Asuransi Jiwa Syariah Jasa Mitra Abadi Tbk")
    assert "ASURANSI JIWA" not in aliases
    assert "ASURANSI JIWA SYARIAH JASA MITRA ABADI" in aliases


def test_connector_fragments_are_rejected() -> None:
    """'Bank of India Indonesia' never yields 'Bank of' or a leading 'of'."""
    aliases = aliases_for("Bank of India Indonesia Tbk")
    assert "BANK OF" not in aliases
    assert not any(a.startswith("OF ") for a in aliases)


def test_generic_words_learns_from_the_corpus() -> None:
    """Words dozens of issuers share are treated as generic without being hand-listed."""
    generic = generic_words(s.company_name for s in load_baseline())
    assert {"INDONESIA", "NUSANTARA", "SEJAHTERA"} <= generic
    assert "KRAKATAU" not in generic


def test_profiles_typing_skips_non_equities_and_bad_identities() -> None:
    """A row without an equity flag or a sane ticker never reaches the registry."""
    payload = {
        "data": [
            {"KodeEmiten": "BBCA", "NamaEmiten": "PT Bank Central Asia Tbk", "EfekEmiten_Saham": True},
            {"KodeEmiten": "BOND", "NamaEmiten": "Some Bond", "EfekEmiten_Saham": False},
            {"KodeEmiten": "TOOLONG", "NamaEmiten": "Bad Ticker", "EfekEmiten_Saham": True},
            {"KodeEmiten": "NONM", "NamaEmiten": "", "EfekEmiten_Saham": True},
        ]
    }
    tickers = {s.ticker for s in securities_from_profiles(payload)}
    assert tickers == {"BBCA", "IHSG"}


def test_an_alias_two_issuers_share_is_dropped_from_both() -> None:
    """An ambiguous name form identifies nobody, so it belongs to nobody."""
    payload = {
        "data": [
            {"KodeEmiten": "AAAA", "NamaEmiten": "Cahaya Terang Sekali", "EfekEmiten_Saham": True},
            {"KodeEmiten": "BBBB", "NamaEmiten": "Cahaya Terang Sekali", "EfekEmiten_Saham": True},
            {"KodeEmiten": "CCCC", "NamaEmiten": "Krakatau Steel", "EfekEmiten_Saham": True},
        ]
    }
    index = alias_index(securities_from_profiles(payload))
    assert normalize("CAHAYA TERANG SEKALI") not in index
    assert index[normalize("KRAKATAU STEEL")] == "CCCC"


def test_csv_round_trip_preserves_every_field() -> None:
    """The committed baseline format loses nothing on the way out and back."""
    original = securities_from_profiles(_fixture_payload())
    assert from_csv(to_csv(original)) == original


def test_a_thin_payload_falls_back_to_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken feed must never empty the registry, or nothing gets scored at all."""
    thin = json.dumps({"data": [{"KodeEmiten": "BBCA", "NamaEmiten": "BCA", "EfekEmiten_Saham": True}]})
    monkeypatch.setattr(refresh, "_newest_payload", lambda minio: thin.encode())
    written: list[tuple[int, str]] = []
    monkeypatch.setattr(refresh, "write", lambda dsn, secs, source: written.append((len(secs), source)))

    outcome = refresh.refresh(cast("Minio", None), "dsn")

    assert outcome.degraded and outcome.source == "baseline"
    assert outcome.count == len(load_baseline())
    assert written == [(outcome.count, "baseline")]


def test_an_unreachable_bronze_falls_back_to_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Object-store trouble degrades the registry, it never blanks it."""
    def _boom(minio: object) -> bytes:
        raise OSError("minio down")

    monkeypatch.setattr(refresh, "_newest_payload", _boom)
    monkeypatch.setattr(refresh, "write", lambda dsn, secs, source: None)

    outcome = refresh.refresh(cast("Minio", None), "dsn")

    assert outcome.degraded and outcome.count == len(load_baseline())
    assert "minio down" in outcome.notes


def test_a_healthy_payload_is_taken_over_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live feed wins whenever it looks like a real listing."""
    monkeypatch.setattr(refresh, "_newest_payload", lambda minio: _FIXTURE.read_bytes())
    monkeypatch.setattr(refresh, "write", lambda dsn, secs, source: None)

    outcome = refresh.refresh(cast("Minio", None), "dsn")

    assert not outcome.degraded and outcome.source == "bronze"


def test_the_real_payload_types_out_above_the_plausible_floor() -> None:
    """The live IDX shape produces a registry the never-empty guard would accept."""
    securities = securities_from_profiles(_fixture_payload())
    assert len(securities) >= MIN_PLAUSIBLE_SECURITIES
    by_ticker = {s.ticker: s for s in securities}
    assert by_ticker["KRAS"].aliases == ("KRAKATAU STEEL",)
    assert by_ticker["IHSG"].board == "INDEX"
