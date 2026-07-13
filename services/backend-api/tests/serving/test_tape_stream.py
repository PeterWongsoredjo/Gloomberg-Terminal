"""The tape streamer: snapshot then deltas, freeze on a closed market, abort a stuck client."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

from fastapi import WebSocketDisconnect

from app.serving.tape.manager import TapeStreamer

_SESSION_1 = datetime(2026, 7, 2, 3, 0, tzinfo=UTC)  # 10:00 WIB, a trading Thursday
_TRADING_DAY = date(2026, 7, 2)
_WEEKEND = date(2026, 7, 4)  # Saturday, market closed


def _row(sid: int, close: int, *, trade_date: date) -> dict[str, Any]:
    return {
        "security_id": sid, "ticker": f"T{sid}", "board": "MAIN", "is_fca": False,
        "special_notation": [], "close_adj_idr": close, "previous_idr": close, "change_idr": 0,
        "change_pct": 0.0, "at_price_limit": "NONE", "volume_shares": 1000, "value_idr": close * 1000,
        "net_foreign_idr": 0, "price_series_integrity": "CLEAN", "data_as_of": datetime.now(UTC),
        "trade_date": trade_date, "dq_flags": [],
    }


class FakeReader:
    """Serves a scripted sequence of tape states, one per read."""

    def __init__(self, states: list[list[dict[str, Any]]]) -> None:
        self._states = states
        self._i = 0

    async def live_tape(self) -> list[dict[str, Any]]:
        state = self._states[min(self._i, len(self._states) - 1)]
        self._i += 1
        return state


class FakeWS:
    """Records frames; can disconnect after N sends or go slow to simulate a stuck client."""

    def __init__(self, *, disconnect_after: int | None = None, slow_after: int | None = None, slow: float = 0.0) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._count = 0
        self._disconnect_after = disconnect_after
        self._slow_after = slow_after
        self._slow = slow

    async def send_json(self, frame: dict[str, Any]) -> None:
        self._count += 1
        if self._slow_after is not None and self._count > self._slow_after:
            await asyncio.sleep(self._slow)
        self.sent.append(frame)
        if self._disconnect_after is not None and len(self.sent) >= self._disconnect_after:
            raise WebSocketDisconnect(1000)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def _streamer(ws: FakeWS, reader: FakeReader, **kw: Any) -> TapeStreamer:
    return TapeStreamer(ws, reader, None, live_poll=0.001, frozen_poll=0.001, now_fn=lambda: _SESSION_1, **kw)  # type: ignore[arg-type]


def _universe(first_close: int) -> list[dict[str, Any]]:
    """A five-security tape where only security 1's price varies between states."""
    rows = [_row(1, first_close, trade_date=_TRADING_DAY)]
    rows += [_row(sid, 500, trade_date=_TRADING_DAY) for sid in range(2, 6)]
    return rows


async def test_snapshot_then_delta_during_session() -> None:
    """The client gets a snapshot first, then a delta for just the row that changed."""
    states = [_universe(100), _universe(101), _universe(102)]  # only security 1 moves
    ws = FakeWS(disconnect_after=3)
    await _streamer(ws, FakeReader(states)).run()

    assert ws.sent[0]["type"] == "snapshot"
    assert len(ws.sent[0]["rows"]) == 5
    deltas = [f for f in ws.sent if f["type"] == "delta"]
    assert deltas, "expected at least one delta after a price change"
    # a single-row change stays a delta, not a full-snapshot fallback
    assert len(deltas[0]["changed"]) == 1
    assert deltas[0]["changed"][0]["security_id"] == 1
    # seq is monotonic so the client can detect a gap
    seqs = [f["seq"] for f in ws.sent if "seq" in f]
    assert seqs == sorted(seqs)


async def test_frozen_market_sends_only_heartbeats() -> None:
    """On a closed day the tape freezes: a snapshot then heartbeats, never a delta."""
    ws = FakeWS(disconnect_after=3)
    reader = FakeReader([[_row(1, 100, trade_date=_WEEKEND)]])
    await TapeStreamer(ws, reader, None, live_poll=0.001, frozen_poll=0.001, now_fn=lambda: _SESSION_1).run()  # type: ignore[arg-type]

    assert ws.sent[0]["type"] == "snapshot"
    assert all(f["type"] != "delta" for f in ws.sent)
    assert any(f["type"] == "heartbeat" for f in ws.sent)


async def test_slow_consumer_is_disconnected() -> None:
    """A client that keeps timing out on sends gets a problem frame and is closed."""
    states = [
        [_row(1, 100, trade_date=_TRADING_DAY)],
        [_row(1, 101, trade_date=_TRADING_DAY)],
        [_row(1, 102, trade_date=_TRADING_DAY)],
        [_row(1, 103, trade_date=_TRADING_DAY)],
    ]
    ws = FakeWS(slow_after=1, slow=0.05)  # snapshot sends fast, every later send stalls
    await _streamer(ws, FakeReader(states), send_timeout=0.01, max_send_timeouts=1).run()

    assert ws.closed is True
    assert ws.sent[-1]["type"] == "error"
    assert ws.sent[-1]["problem"]["status"] == 408
