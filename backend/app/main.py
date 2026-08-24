"""ReconAgent backend — FastAPI application factory and ASGI entrypoint."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.ai import reset_ai_agent, router as ai_router
from app.api.finance import router as finance_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.db.session import DatabaseNotConfiguredError, reset_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Starting %s v%s (environment=%s)",
        settings.app_name,
        settings.version,
        settings.environment,
    )
    yield
    reset_ai_agent()
    reset_engine()
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "ReconAgent backend — AI finance controller over a normalized "
            "internal financial data model (synthetic dataset for "
            "deterministic demos and evaluation)."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(finance_router)
    app.include_router(ai_router)

    @app.exception_handler(DatabaseNotConfiguredError)
    async def database_not_configured_handler(
        request: Request, exc: DatabaseNotConfiguredError
    ) -> JSONResponse:
        """Unconfigured DATABASE_URL is a deployment state, not a crash:
        every DB-backed route answers with a clear 503."""
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
