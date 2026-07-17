import asyncio
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.ratelimit import RateLimitMiddleware
from app.lifespan import lifespan

# psycopg's async checkpointer needs a selector loop; Windows defaults to a proactor one
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_app() -> FastAPI:
    """Builds the FastAPI app: routers, error handling, and the lifespan resource seam."""
    app = FastAPI(
        title="Gloomberg Terminal API",
        version="v1",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_terminal_origin],  # web-terminal only, no wildcard (SV-10)
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # per-IP ceiling, in-memory: fine single-process, needs Redis if ever multi-process
    app.state.limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[settings.rate_limit] if settings.rate_limit else [],
        enabled=bool(settings.rate_limit),  # empty limit turns the ceiling off, dev only
    )
    app.add_middleware(RateLimitMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
