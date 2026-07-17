from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class Problem(BaseModel):
    """RFC 9457 problem+json shape, never carries stack traces, SQL, or paths."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    correlation: dict[str, Any] | None = None


def problem_response(title: str, status_code: int, request: Request) -> JSONResponse:
    """Renders a Problem as the RFC 9457 problem+json response shape."""
    problem = Problem(title=title, status=status_code, instance=str(request.url.path))
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wires every error response to the RFC 9457 problem+json contract."""

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        title = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return problem_response(title, exc.status_code, request)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response("Internal server error", 500, request)
