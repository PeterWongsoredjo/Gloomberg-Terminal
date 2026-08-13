"""
Turns a quoted rupiah amount into integer cents.

IDX filings are bilingual and the same document writes an amount both ways, so
"Rp2,5" and "IDR2.5" both mean two and a half rupiah. Neither separator can be
assumed to mean one thing. Anything this cannot read raises, never guesses.
"""

from __future__ import annotations

import re

_LEADING_CURRENCY = re.compile(r"^(?:rp|idr|usd)\.?", re.IGNORECASE)
_TRAILING_DASH = re.compile(r"[,.]-$")
_DIGITS_AND_SEPARATORS = re.compile(r"^[\d.,]+$")

SEN_PER_RUPIAH = 100
MAX_DECIMALS = 2


def _split(cleaned: str) -> tuple[str, str]:
    """The whole and fractional parts, deciding which separator is the decimal one."""
    separators = {char for char in cleaned if char in ".,"}
    if not separators:
        return cleaned, ""

    last = max(cleaned.rfind("."), cleaned.rfind(","))
    tail = cleaned[last + 1 :]
    if not tail.isdigit():
        raise ValueError(f"amount ends in a separator: {cleaned!r}")

    # two different separators means the last one has to be the decimal
    if len(separators) == 1 and (cleaned.count(next(iter(separators))) > 1 or len(tail) == 3):
        return re.sub(r"[.,]", "", cleaned), ""
    if len(tail) > MAX_DECIMALS:
        raise ValueError(f"amount has more than two decimals: {cleaned!r}")
    return re.sub(r"[.,]", "", cleaned[:last]), tail


def amount_sen(amount_text: str, currency: str) -> int | None:
    """Rupiah cents from a quoted amount, none when the filing is not in rupiah."""
    if currency != "IDR":
        return None

    cleaned = amount_text.strip().replace("\xa0", " ")
    cleaned = _LEADING_CURRENCY.sub("", cleaned).strip().replace(" ", "")
    cleaned = _TRAILING_DASH.sub("", cleaned)
    if not cleaned or not _DIGITS_AND_SEPARATORS.fullmatch(cleaned):
        raise ValueError(f"unreadable rupiah amount: {amount_text!r}")

    whole, fraction = _split(cleaned)
    if not whole.isdigit():
        raise ValueError(f"unreadable rupiah amount: {amount_text!r}")
    return int(whole) * SEN_PER_RUPIAH + int(fraction.ljust(MAX_DECIMALS, "0") or 0)
