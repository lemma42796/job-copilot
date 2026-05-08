"""FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobcopilot_api import __version__
from jobcopilot_api.errors import install_exception_handlers
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.infra.logging import setup_logging
from jobcopilot_api.infra.prompts import load_prompt_versions
from jobcopilot_api.infra.request_id import RequestIDMiddleware
from jobcopilot_api.routers import health
from jobcopilot_api.settings import settings

# Langfuse SDK 走 LANGFUSE_* 命名,本项目 settings 走 JOBCOPILOT_ 前缀;
# 这里把字段镜像到 os.environ 让 langfuse.openai 自动读取。
# public_key 留空 → SDK 走 noop(8-ENGINEERING §11.3)。
if settings.langfuse_public_key:
    os.environ.setdefault(
        "LANGFUSE_HOST", settings.langfuse_host or "http://localhost:3001"
    )
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ADR-0006 D6: scan prompts/, upsert prompt_versions, cache by
    # (agent_name, version) on app.state for agents to look up.
    app.state.prompt_versions = await load_prompt_versions(get_sessionmaker())
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
