"""Unit tests for resolving headlines to the issuers they are actually about."""

from __future__ import annotations

import pytest

from pipeline.reference.matcher import Registry
from pipeline.reference.securities import load_baseline


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry(load_baseline())


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("Terbaru! AMRO Proyeksi Ekonomi RI Tumbuh 5% di 2026", ""),
        ("Profil Direktur RANS yang Mundur", ""),
        ("UMKM Teriak, Bisnis Makin Susah", ""),
        ("Begini Cara Membersihkan Nama dari SLIK OJK", ""),
        ("RI Kena Tarif Tambahan 10% dari AS", "laporan USTR terbaru"),
        ("Airlangga Pastikan PFII Bebas dari 'Dana Gelap'", ""),
        ("Allianz Akuisisi Unit Asuransi HSBC di Negeri Singa", ""),
    ],
)
def test_unlisted_four_letter_tokens_never_tag(registry: Registry, title: str, summary: str) -> None:
    """A word that looks like a code but is not listed on IDX resolves to nothing."""
    assert registry.match(title, summary).tickers == []


def test_ordinary_indonesian_words_are_not_codes(registry: Registry) -> None:
    """The code pass stays case-sensitive, so 'naik' and 'laba' are just words."""
    result = registry.match("Pendapatan naik tapi laba turun, data buka suara", "")
    assert result.tickers == []


def test_a_lowercase_word_matching_a_ticker_is_ignored(registry: Registry) -> None:
    """'data' in prose is not the issuer DATA; only the capitalised code counts."""
    assert registry.match("Menurut data terbaru pasar melemah", "").tickers == []
    assert registry.match("DATA Buka Suara soal Saham Protelindo", "").tickers == ["DATA"]


def test_tickers_follow_the_order_of_the_text(registry: Registry) -> None:
    """The first issuer named leads, so the feed can trust tickers[0]."""
    assert registry.match("BBCA dan BBRI kompak menguat", "").tickers == ["BBCA", "BBRI"]
    assert registry.match("BBRI dan BBCA kompak menguat", "").tickers == ["BBRI", "BBCA"]


def test_issuers_named_in_prose_resolve(registry: Registry) -> None:
    """Most Indonesian headlines spell the company out instead of using its code."""
    assert registry.match("Pabrik Kebakaran, Krakatau Steel Ungkap Penyebab", "").tickers == ["KRAS"]
    assert "ANTM" in registry.match("Antam Melesat di Sesi Pertama", "").tickers


def test_the_index_is_an_ordinary_subject(registry: Registry) -> None:
    """IHSG resolves like any other ticker, which is what gives it sentiment."""
    assert registry.match("IHSG Loyo, Asing Jualan Saham", "").tickers == ["IHSG"]


def test_the_summary_is_searched_too(registry: Registry) -> None:
    """A code that only appears in the body still tags the article."""
    assert registry.match("Laba emiten ini rontok", "Ancora Indonesia (OKAS) turun").tickers == ["OKAS"]


def test_candidates_report_raw_codes_even_when_unresolved(registry: Registry) -> None:
    """The unvalidated tokens still travel, as hints for the model and for audit."""
    result = registry.match("Saham RANS dan PFII melonjak", "")
    assert result.tickers == []
    assert result.candidates == ["RANS", "PFII"]


def test_an_empty_registry_resolves_nothing_without_crashing() -> None:
    """A registry that failed to load degrades to no tags, never to an exception."""
    empty = Registry([])
    assert empty.match("BBCA menguat", "").tickers == []
    assert empty.match("BBCA menguat", "").candidates == ["BBCA"]
