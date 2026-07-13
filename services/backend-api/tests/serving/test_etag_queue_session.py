"""The small serving primitives: ETag conditional GET, tape coalescing, and freeze rules."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import SessionPhase
from app.serving.etag import make_etag, not_modified
from app.serving.tape.queue import CoalescingBuffer
from app.serving.tape.session import is_frozen

_AS_OF = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)


def test_etag_is_stable_and_query_sensitive() -> None:
    assert make_etag(_AS_OF, "q=1") == make_etag(_AS_OF, "q=1")
    assert make_etag(_AS_OF, "q=1") != make_etag(_AS_OF, "q=2")


def test_not_modified_matches_prior_tag() -> None:
    tag = make_etag(_AS_OF, "q=1")
    assert not_modified(tag, tag) is True
    assert not_modified('"stale"', tag) is False
    assert not_modified(None, tag) is False


def test_buffer_coalesces_latest_wins() -> None:
    buffer = CoalescingBuffer()
    buffer.push([{"security_id": 1, "v": 100}, {"security_id": 2, "v": 200}])
    buffer.push([{"security_id": 1, "v": 111}])  # newer state for security 1
    assert len(buffer) == 2
    by_id = {r["security_id"]: r["v"] for r in buffer.peek()}
    assert by_id == {1: 111, 2: 200}
    buffer.clear()
    assert len(buffer) == 0


def test_frozen_only_when_market_not_trading() -> None:
    assert is_frozen(SessionPhase.CLOSED) is True
    assert is_frozen(SessionPhase.SESSION_BREAK) is True
    assert is_frozen(SessionPhase.POST_TRADING) is True
    assert is_frozen(SessionPhase.SESSION_1) is False
    assert is_frozen(SessionPhase.SESSION_2) is False
    assert is_frozen(SessionPhase.PRE_OPENING) is False
