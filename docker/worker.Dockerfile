# syntax=docker/dockerfile:1.7
# P4:worker 进程独立成一个镜像/服务。
# 跟 api.Dockerfile 同一套依赖安装步骤,只有 CMD 不同 —— worker 不监听端口,
# 只消费队列,所以可以按队列深度独立扩副本(compose 的 deploy.replicas),
# 不受 API 副本数牵连。
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --package jobcopilot-api

COPY apps/api ./apps/api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package jobcopilot-api

ENV PATH="/app/.venv/bin:${PATH}"

# 没有 HTTP 端口,健康检查就看进程还在不在。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD pgrep -f "jobcopilot_api.workers.main" > /dev/null || exit 1

CMD ["python", "-m", "jobcopilot_api.workers.main"]
