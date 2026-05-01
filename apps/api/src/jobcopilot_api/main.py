"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobcopilot_api import __version__
from jobcopilot_api.errors import install_exception_handlers
from jobcopilot_api.infra.logging import setup_logging
from jobcopilot_api.infra.request_id import RequestIDMiddleware
from jobcopilot_api.routers import health
from jobcopilot_api.settings import settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        title="JobCopilot API",
        version=__version__,
        description="AI 求职副驾 — REST + SSE backend",
        docs_url="/v1/docs" if settings.env != "prod" else None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )

    # Innermost first: CORS sits next to the route handlers; RequestID
    # wraps everything so all logs and error responses see the id.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    install_exception_handlers(app)

    app.include_router(health.router, prefix="/v1")

    return app


app = create_app()
