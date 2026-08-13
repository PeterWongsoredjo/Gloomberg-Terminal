"""Unit tests for reading a quoted rupiah amount into integer cents."""

from __future__ import annotations

import pytest

from app.agentic.amount import amount_sen


def test_the_real_filing_amounts_both_ways() -> None:
    """One IDX filing writes the same amount in Indonesian and English, and both mean 2.5."""
    assert amount_sen("Rp2,5", "IDR") == 250
    assert amount_sen("IDR2.5", "IDR") == 250


def test_the_real_filing_totals_both_ways() -> None:
    """The same document writes its total with dots and with commas as thousands."""
    assert amount_sen("Rp40.939.000.000", "IDR") == 4093900000000
    assert amount_sen("IDR40,939,000,000", "IDR") == 4093900000000


def test_a_trailing_dash_is_not_a_decimal() -> None:
    """Indonesian invoices end an amount with a dash meaning no cents follow."""
    assert amount_sen("Rp1.250,-", "IDR") == 125000


def test_two_decimal_places_survive() -> None:
    """Twelve rupiah fifty is not twelve rupiah, so the cents have to carry."""
    assert amount_sen("Rp 12,50", "IDR") == 1250


def test_mixed_separators_read_the_last_one_as_the_decimal() -> None:
    """With both separators present the final one can only be the decimal point."""
    assert amount_sen("Rp1,250.50", "IDR") == 125050


def test_a_plain_number_needs_no_separator() -> None:
    """An amount written without punctuation is whole rupiah."""
    assert amount_sen("Rp100", "IDR") == 10000


def test_a_foreign_amount_has_no_rupiah_cents() -> None:
    """Some issuers report in dollars, and inventing a rupiah value would be a lie."""
    assert amount_sen("USD 0.0035", "USD") is None


def test_prose_is_never_guessed_into_a_number() -> None:
    """A model that answers in words must fail loudly, never resolve to some amount."""
    with pytest.raises(ValueError):
        amount_sen("about a hundred rupiah", "IDR")


def test_three_decimals_fail_closed() -> None:
    """Rupiah has no third decimal, so this is a misread and must not be rounded."""
    with pytest.raises(ValueError):
        amount_sen("Rp1.234,567", "IDR")


def test_an_empty_amount_fails_closed() -> None:
    """No amount is not zero rupiah."""
    with pytest.raises(ValueError):
        amount_sen("Rp", "IDR")


def test_a_dangling_separator_fails_closed() -> None:
    """A truncated amount is unreadable, not a whole number."""
    with pytest.raises(ValueError):
        amount_sen("Rp2,", "IDR")
