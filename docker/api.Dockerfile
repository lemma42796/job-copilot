# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv (pinned)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Install only what's needed to resolve the workspace lock first (cache layer).
COPY pyproject.toml uv.lock* ./
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --package jobcopilot-api

# Copy the rest of the API source and install it
COPY apps/api ./apps/api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package jobcopilot-api

EXPOSE 8000

# Use the venv python directly so we don't rely on `uv run` at runtime.
ENV PATH="/app/.venv/bin:${PATH}"

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health',timeout=2).status==200 else 1)"

# P5:单进程 uvicorn 换成 gunicorn 多进程。Python 有 GIL,单个 uvicorn 进程
# 的 JSON 序列化、Pydantic 校验、SSE 编码都挤在一个核上;worker 数按容器可用
# CPU 给。每个 worker 各自持有一个 SQLAlchemy 连接池,所以
# workers × (db_pool_size + db_max_overflow) 必须小于 PostgreSQL 的
# max_connections —— 默认 4 × (20 + 20) = 160,搭配 docker/postgres 的
# max_connections=300 有余量。改任一侧前先重算这个乘积。
CMD ["gunicorn", "jobcopilot_api.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--access-logfile", "-"]
