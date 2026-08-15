"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
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
from jobcopilot_api.routers import health, jd, notes, quiz  # noqa: E402
from jobcopilot_api.services import jd_service  # noqa: E402
from jobcopilot_api.workers import embed_worker  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ADR-0006 D6: scan prompts/, upsert prompt_versions, cache by
    # (agent_name, version) on app.state for agents to look up.
    app.state.prompt_versions = await load_prompt_versions(get_sessionmaker())

    # 后台 embed worker — 笔记入库时 embedding 留 NULL,worker 异步补
    # (docs/TECH_DESIGN.md)。stop_event 让 shutdown 干净退出。
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        embed_worker.run_forever(stop_event), name="embed_worker"
    )
    app.state.embed_worker_stop = stop_event
    app.state.embed_worker_task = worker_task

    # JD 分析执行与 SSE 观察解耦。进程重启后复用持久化 jd_ids 恢复
    # in_progress 记录;单进程 MVP 不引入外部任务队列或 lease。
    await jd_service.recover_in_progress_analyses(get_sessionmaker())

    try:
        yield
    finally:
        await jd_service.shutdown_analysis_tasks()
        stop_event.set()
        try:
            await asyncio.wait_for(worker_task, timeout=10.0)
        except asyncio.TimeoutError:
            worker_task.cancel()


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
    app.include_router(notes.router, prefix="/api")
    app.include_router(quiz.router, prefix="/api")
    app.include_router(jd.router, prefix="/api")

    return app


app = create_app()
