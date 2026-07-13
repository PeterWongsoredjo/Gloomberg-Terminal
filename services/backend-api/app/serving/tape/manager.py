"""
Streams the Live Tape to one WebSocket client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.enums import QualityFlag, SessionPhase
from app.observability.calendar import get_calendar
from app.observability.slo.engine import SloEngine
from app.serving import mappers
from app.serving.envelope import DATASET_DAILY_TRADE, ct011_header
from app.serving.readers.postgres import ServingPostgresReader
from app.serving.tape import frames
from app.serving.tape.queue import CoalescingBuffer
from app.serving.tape.session import is_frozen

logger = logging.getLogger("gloomberg.serving.tape")


class TapeStreamer:
    """Drives one client's tape stream from the live_tape projection."""

    def __init__(
        self,
        websocket: WebSocket,
        reader: ServingPostgresReader,
        slo_engine: SloEngine | None,
        *,
        live_poll: float = 2.0,
        frozen_poll: float = 15.0,
        send_timeout: float = 5.0,
        max_send_timeouts: int = 3,
        snapshot_fallback_ratio: float = 0.5,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._ws = websocket
        self._reader = reader
        self._slo = slo_engine
        self._live_poll = live_poll
        self._frozen_poll = frozen_poll
        self._send_timeout = send_timeout
        self._max_timeouts = max_send_timeouts
        self._fallback_ratio = snapshot_fallback_ratio
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._buffer = CoalescingBuffer()
        self._last: dict[int, str] = {}  # security_id -> last-sent serialized row
        self._seq = 0
        self._timeouts = 0
        self._trade_date = datetime.now(UTC).date()

    async def run(self) -> None:
        """Sends the initial snapshot then streams until the client leaves or stalls."""
        try:
            rows, mapped, as_of, flags = await self._read()
            self._trade_date = self._resolve_date(rows)
            await self._send(frames.snapshot_frame(mapped, self._header(as_of, flags), self._next_seq()))
            self._last = {int(m["security_id"]): _serialize(m) for m in mapped}
            last_phase = self._phase()
            while True:
                await self._tick(last_phase)
                last_phase = self._phase()
        except WebSocketDisconnect:
            logger.debug("tape client disconnected")
        except _StreamAborted:
            logger.info("tape stream aborted after repeated slow sends")

    async def _tick(self, last_phase: SessionPhase) -> None:
        """One streaming step: emit a phase change, then a delta, snapshot, or heartbeat."""
        phase = self._phase()
        if phase != last_phase:
            await self._send(frames.market_state_frame(phase.value, self._header_now(), self._next_seq()))
        if is_frozen(phase):
            await self._send(frames.heartbeat_frame(self._next_seq()))
            await asyncio.sleep(self._frozen_poll)
            return

        rows, mapped, as_of, flags = await self._read()
        self._trade_date = self._resolve_date(rows)
        changed = [m for m in mapped if _serialize(m) != self._last.get(int(m["security_id"]))]
        self._buffer.push(changed)
        if not len(self._buffer):
            await self._send(frames.heartbeat_frame(self._next_seq()))
            await asyncio.sleep(self._live_poll)
            return

        header = self._header(as_of, flags)
        fallback = len(self._buffer) >= max(1, int(len(mapped) * self._fallback_ratio))
        frame = (
            frames.snapshot_frame(mapped, header, self._next_seq())
            if fallback
            else frames.delta_frame(self._buffer.peek(), header, self._next_seq())
        )
        await self._flush(frame, mapped if fallback else self._buffer.peek())
        await asyncio.sleep(self._live_poll)

    async def _flush(self, frame: dict[str, Any], applied: list[dict[str, Any]]) -> None:
        """Sends a data frame, retrying on timeout by keeping the buffer for the next tick."""
        if await self._try_send(frame):
            for m in applied:
                self._last[int(m["security_id"])] = _serialize(m)
            self._buffer.clear()
            self._timeouts = 0
            return
        self._timeouts += 1
        if self._timeouts > self._max_timeouts:
            problem = {
                "type": "https://gloomberg/errors/slow-consumer",
                "title": "Tape stream fell behind",
                "status": 408,
                "detail": "The client could not keep up with the tape, reconnect to resync.",
            }
            await self._raw_send(frames.error_frame(problem))
            await self._ws.close()
            raise _StreamAborted

    async def _read(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime, list[QualityFlag]]:
        """Reads the current tape and maps it to rows plus the envelope's as-of and flags."""
        rows = await self._reader.live_tape()
        mapped = [mappers.tape_row(r).model_dump(mode="json") for r in rows]
        as_of = max((r["data_as_of"] for r in rows if r.get("data_as_of")), default=datetime.now(UTC))
        flags = sorted({QualityFlag(f) for m in mapped for f in m["dq_flags"]}, key=lambda f: f.value)
        return rows, mapped, as_of, flags

    def _header(self, as_of: datetime, flags: list[QualityFlag]) -> dict[str, Any]:
        return ct011_header(
            data_as_of=as_of,
            trade_date=self._trade_date,
            quality_flags=flags,
            dataset=DATASET_DAILY_TRADE,
            slo_engine=self._slo,
        )

    def _header_now(self) -> dict[str, Any]:
        return self._header(datetime.now(UTC), [])

    def _phase(self) -> SessionPhase:
        return get_calendar().market_state(self._trade_date, self._now())

    def _resolve_date(self, rows: list[dict[str, Any]]) -> date:
        return max((r["trade_date"] for r in rows if r.get("trade_date")), default=self._trade_date)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _send(self, frame: dict[str, Any]) -> None:
        """Sends a control/snapshot frame, a timeout here is treated as a lost client."""
        if not await self._try_send(frame):
            raise _StreamAborted

    async def _try_send(self, frame: dict[str, Any]) -> bool:
        """Sends within the timeout, False on timeout so the caller can coalesce and retry."""
        try:
            await asyncio.wait_for(self._raw_send(frame), timeout=self._send_timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _raw_send(self, frame: dict[str, Any]) -> None:
        await self._ws.send_json(frame)


class _StreamAborted(Exception):
    """Internal signal that the stream ended for a non-disconnect reason."""


def _serialize(mapped_row: dict[str, Any]) -> str:
    """A stable string of a row, to detect whether it changed since the last frame."""
    return json.dumps(mapped_row, sort_keys=True)
