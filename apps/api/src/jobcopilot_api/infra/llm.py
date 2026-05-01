"""LLMClient assembly + FastAPI dependency.

Mirrors the lazy-cache style of `infra.db`: a process-level singleton built
on first access, so endpoints share one underlying connection pool. Tests
override via `app.dependency_overrides[get_llm_client]`.

The default `CallLogger` is `DBCallLogger`, which writes every call to the
`llm_calls` table on its own session (see `llm.db_logger` for the rationale).
"""

from __future__ import annotations

from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.llm.client import BaseLLMClient, CallLogger, LLMClient
from jobcopilot_api.llm.db_logger import DBCallLogger
from jobcopilot_api.llm.providers.dashscope import DashscopeProvider
from jobcopilot_api.settings import settings

_client: LLMClient | None = None


def _build_default_client() -> LLMClient:
    provider = DashscopeProvider(api_key=settings.dashscope_api_key)
    logger: CallLogger = DBCallLogger(sessionmaker=get_sessionmaker())
    return BaseLLMClient(provider=provider, logger=logger)


def get_llm_client() -> LLMClient:
    """FastAPI dependency. First call constructs the singleton; later calls
    re-use it. Raises `ValueError` if `JOBCOPILOT_DASHSCOPE_API_KEY` is empty."""
    global _client
    if _client is None:
        _client = _build_default_client()
    return _client


def reset_client() -> None:
    """Test helper: drop the cached client so the next `get_llm_client()`
    rebuilds with current settings (or with a test-injected provider)."""
    global _client
    _client = None
