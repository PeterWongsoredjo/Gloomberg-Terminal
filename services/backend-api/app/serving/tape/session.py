"""When the tape streams versus freezes, by session phase."""

from __future__ import annotations

from app.core.enums import SessionPhase

# during these phases the market is not trading, so the tape freezes to heartbeats only
_FROZEN_PHASES = frozenset({SessionPhase.SESSION_BREAK, SessionPhase.POST_TRADING, SessionPhase.CLOSED})


def is_frozen(phase: SessionPhase) -> bool:
    """True when the tape should freeze (closed/break): no deltas, heartbeat and phase only."""
    return phase in _FROZEN_PHASES
