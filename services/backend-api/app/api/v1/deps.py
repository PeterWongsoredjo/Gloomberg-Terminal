"""
Authentication and dependency injection for FastAPI endpoints.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

from fastapi import Depends, Header, HTTPException, status
from starlette.requests import HTTPConnection

from app.core.config import settings
from app.lifespan import AppState
from app.serving.readers.gold import ServingGoldReader


async def require_operator(authorization: str | None = Header(default=None)) -> None:
    """Gates a request behind the stub operator token, or trusts loopback when none is set."""
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid operator token")


def get_app_state(connection: HTTPConnection) -> AppState:
    """Returns the process-scoped resources the lifespan yielded, works for REST and WebSocket."""
    return cast(AppState, connection.state.app_state)


def require_agentic(app_state: AppState = Depends(get_app_state)) -> AppState:
    """Yields app state only when the agentic layer came up, else 503."""
    if app_state.compiled_graph is None or app_state.agentic_deps is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="agentic layer unavailable"
        )
    return app_state


async def anchor_trade_date(app_state: AppState) -> date:
    """The latest trade_date in Gold, or today, anchors the envelope's session phase."""
    latest = await ServingGoldReader(app_state.duckdb_ro).latest_trade_date()
    return date.fromisoformat(latest) if latest else datetime.now(UTC).date()
