"""FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jobcopilot_api import __version__
from jobcopilot_api.settings import settings

# Langfuse SDK 走 LANGFUSE_* 命名,本项目 settings 走 JOBCOPILOT_ 前缀;
# 这里把字段镜像到 os.environ 让 langfuse.openai 自动读取。
# **必须在 routers / agents / llm 这些会 import langfuse.openai 的模块之前执行**:
# langfuse.openai 在 import 时读环境变量,读不到 PUBLIC_KEY 就进 noop 模式,trace 不进。
# public_key 留空 → SDK 走 noop(README / AGENTS.md)。
if settings.langfuse_public_key:
    os.environ.setdefault(
        "LANGFUSE_HOST", settings.langfuse_host or "http://localhost:3001"
    )
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)

from jobcopilot_api.errors import install_exception_handlers  # noqa: E402
from jobcopilot_api.infra.db import get_sessionmaker  # noqa: E402
from jobcopilot_api.infra.logging import setup_logging  # noqa: E402
from jobcopilot_api.infra.prompts import load_prompt_versions  # noqa: E402
from jobcopilot_api.infra.request_id import RequestIDMiddleware  # noqa: E402
from jobcopilot_api.routers import auth, health, jd, jobs, notes, quiz  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ADR-0006 D6: scan prompts/, upsert prompt_versions, cache by
    # (agent_name, version) on app.state for agents to look up.
    app.state.prompt_versions = await load_prompt_versions(get_sessionmaker())

    # P4:API 进程不再跑任何后台任务。embed worker、job worker、超期回收
    # 都搬到独立的 worker 容器(`python -m jobcopilot_api.workers.main`)。
    # API 多进程 / 多副本后,后台任务挂在 lifespan 里会被每个进程各跑一份,
    # 既重复调用上游也重复扣费。
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

    # health 暂留 /v1(docker/api.Dockerfile healthcheck 引用 /v1/health,
    # 切换到 /api 是单独切片,避免 M1 改部署链路)。新业务模块按 OpenAPI / Pydantic schemas
    # 统一挂 /api。
    app.include_router(health.router, prefix="/v1")
    app.include_router(auth.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(notes.router, prefix="/api")
    app.include_router(quiz.router, prefix="/api")
    app.include_router(jd.router, prefix="/api")

    return app


app = create_app()
