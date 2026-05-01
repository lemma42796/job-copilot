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

CMD ["uvicorn", "jobcopilot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
