"""Cursor pagination is opaque, stable, and rejects tampered or unknown input."""

from __future__ import annotations

import pytest

from app.serving.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Cursor,
    CursorError,
    clamp_limit,
    decode_cursor,
    decode_str_cursor,
    encode_cursor,
    encode_str_cursor,
    resolve_sort,
)


def test_cursor_round_trips() -> None:
    cursor = Cursor(sort_value="BBCA", security_id=12345)
    assert decode_cursor(encode_cursor(cursor)) == cursor


def test_decode_none_is_first_page() -> None:
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


def test_malformed_cursor_raises() -> None:
    with pytest.raises(CursorError):
        decode_cursor("not-valid-base64!!")


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, DEFAULT_LIMIT), (0, 1), (-5, 1), (50, 50), (10_000, MAX_LIMIT)],
)
def test_limit_is_clamped(given: int | None, expected: int) -> None:
    assert clamp_limit(given) == expected


def test_resolve_sort_direction() -> None:
    assert resolve_sort("ticker") == ("ticker", False)
    assert resolve_sort("-ticker") == ("ticker", True)
    assert resolve_sort(None) == ("ticker", False)


def test_resolve_sort_rejects_unknown_key() -> None:
    with pytest.raises(CursorError):
        resolve_sort("value_idr")


def test_string_cursor_round_trips() -> None:
    token = encode_str_cursor("2026-07-03 09:00:00|abc")
    assert decode_str_cursor(token) == "2026-07-03 09:00:00|abc"


def test_malformed_string_cursor_raises() -> None:
    with pytest.raises(CursorError):
        decode_str_cursor("@@@not base64@@@")
