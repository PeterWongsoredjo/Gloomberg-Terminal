from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.lifespan import lifespan


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
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
