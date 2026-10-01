"""
Times every HTTP request and labels it by route template, never the raw URL.

The template is read after the router has matched, so /runs/abc and /runs/xyz
both count as /api/v1/runs/{run_id}. WebSockets pass straight through.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import UNMATCHED_ROUTE, observe_http


def route_template(request: Request) -> str:
    """The matched route's full path pattern, or one shared label when none matched."""
    template = getattr(request.scope.get("route"), "path_format", None)
    if not isinstance(template, str):
        return UNMATCHED_ROUTE
    segments = request.scope["path"].split("/")
    prefix = "/".join(segments[: max(len(segments) - template.count("/"), 1)])
    params = request.scope.get("path_params") or {}
    values = {part for value in params.values() for part in str(value).split("/") if part}
    if values & set(prefix.split("/")):
        return template
    return prefix + template


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            observe_http(request.method, route_template(request), 500, time.perf_counter() - started)
            raise
        observe_http(
            request.method, route_template(request), response.status_code, time.perf_counter() - started
        )
        return response
