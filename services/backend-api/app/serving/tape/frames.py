"""The five WebSocket message types, each carrying the freshness envelope at the frame level."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def snapshot_frame(rows: list[dict[str, Any]], header: dict[str, Any], seq: int) -> dict[str, Any]:
    """The full tape, sent on connect and as a resync after a gap."""
    return {"type": "snapshot", "envelope": header, "rows": rows, "seq": seq}


def delta_frame(changed: list[dict[str, Any]], header: dict[str, Any], seq: int) -> dict[str, Any]:
    """Only the rows that changed since the last frame; applied by security_id."""
    return {"type": "delta", "envelope": header, "changed": changed, "seq": seq}


def market_state_frame(state: str, header: dict[str, Any], seq: int) -> dict[str, Any]:
    """A session-phase transition; the tape freezes or resumes off this."""
    return {"type": "market_state", "envelope": header, "state": state, "seq": seq}


def heartbeat_frame(seq: int, now: datetime | None = None) -> dict[str, Any]:
    """Liveness plus the current seq, so a client detects a gap even when nothing changed."""
    return {"type": "heartbeat", "server_time": (now or datetime.now(UTC)).isoformat(), "seq": seq}


def error_frame(problem: dict[str, Any]) -> dict[str, Any]:
    """A terminal problem+json frame telling the client to reconnect."""
    return {"type": "error", "problem": problem}
