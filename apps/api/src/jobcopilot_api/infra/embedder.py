"""Embedder assembly + FastAPI dependency. Mirrors `infra.llm`.

A process-level singleton built lazily on first access so all routes
share one underlying httpx client. Tests override via
`app.dependency_overrides[get_embedder]` (or via the `_embedder_dep`
indirection on the profiles router, same pattern as `_llm_dep`).
"""

from __future__ import annotations

from jobcopilot_api.llm.embedders import DashscopeEmbedder, Embedder
from jobcopilot_api.settings import settings

_embedder: Embedder | None = None


def _build_default_embedder() -> Embedder:
    return DashscopeEmbedder(api_key=settings.dashscope_api_key)


def get_embedder() -> Embedder:
    """FastAPI dependency. First call constructs the singleton; later calls
    re-use it. Raises `ValueError` if `JOBCOPILOT_DASHSCOPE_API_KEY` is empty."""
    global _embedder
    if _embedder is None:
        _embedder = _build_default_embedder()
    return _embedder


def reset_embedder() -> None:
    """Test helper: drop the cached embedder so the next `get_embedder()`
    rebuilds with current settings (or with a test-injected instance)."""
    global _embedder
    _embedder = None
