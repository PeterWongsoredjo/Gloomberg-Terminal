"""
Authentication and dependency injection for FastAPI endpoints.
"""

from __future__ import annotations

from typing import cast

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.lifespan import AppState


async def require_operator(authorization: str | None = Header(default=None)) -> None:
    """Gates a request behind the stub operator token, or trusts loopback when none is set."""
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid operator token")


def get_app_state(request: Request) -> AppState:
    """Returns the process-scoped resources the lifespan yielded onto request state."""
    return cast(AppState, request.state.app_state)


def require_agentic(app_state: AppState = Depends(get_app_state)) -> AppState:
    """Yields app state only when the agentic layer came up, else 503."""
    if app_state.compiled_graph is None or app_state.agentic_deps is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="agentic layer unavailable"
        )
    return app_state
