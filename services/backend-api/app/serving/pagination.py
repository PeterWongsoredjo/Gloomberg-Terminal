"""
Cursor pagination for the universe list.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

SORT_KEYS = frozenset({"ticker"})
DEFAULT_SORT = "ticker"


class CursorError(ValueError):
    """A malformed or tampered cursor, the endpoint turns this into a 400."""


@dataclass(frozen=True)
class Cursor:
    sort_value: Any
    security_id: int


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def resolve_sort(sort: str | None) -> tuple[str, bool]:
    raw = sort or DEFAULT_SORT
    descending = raw.startswith("-")
    column = raw[1:] if descending else raw
    if column not in SORT_KEYS:
        raise CursorError(f"unknown sort key: {column}")
    return column, descending


def encode_cursor(cursor: Cursor) -> str:
    raw = json.dumps({"v": cursor.sort_value, "id": cursor.security_id}, default=str).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(token: str | None) -> Cursor | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload = json.loads(raw)
        return Cursor(sort_value=payload["v"], security_id=int(payload["id"]))
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CursorError("malformed cursor") from exc


def encode_str_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def decode_str_cursor(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return base64.urlsafe_b64decode(token.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise CursorError("malformed cursor") from exc
