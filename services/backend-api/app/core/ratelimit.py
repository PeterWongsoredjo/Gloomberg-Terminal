"""
Per-IP rate limiting over slowapi's Limiter.

slowapi's own middleware finds routes via route.endpoint, which this Starlette
no longer exposes, so it silently exempts everything. This thin dispatch keeps
all counting inside slowapi and only replaces that broken discovery step.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import problem_response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limiter: Limiter = request.app.state.limiter
        if not limiter.enabled:
            return await call_next(request)
        try:
            # keyed per IP and path by the limiter's url key style
            limiter._check_request_limit(request, None, True)
        except RateLimitExceeded as exc:
            return problem_response(f"Rate limit exceeded: {exc.detail}", 429, request)
        return await call_next(request)
