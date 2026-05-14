"""Langfuse helpers shared by manual instrumentation and OpenAI clients."""

from __future__ import annotations

import os
from typing import Any

from langfuse import Langfuse

from jobcopilot_api.settings import settings

_langfuse: Langfuse | None = None


class NoopGeneration:
    def end(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update(self, *args: Any, **kwargs: Any) -> None:
        return None


def _public_key() -> str:
    return settings.langfuse_public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")


def _secret_key() -> str:
    return settings.langfuse_secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")


def langfuse_configured() -> bool:
    return bool(_public_key() and _secret_key())


def _host() -> str | None:
    return settings.langfuse_host or os.environ.get("LANGFUSE_HOST") or None


def get_langfuse() -> Langfuse | None:
    global _langfuse
    if not langfuse_configured():
        return None
    if _langfuse is None:
        kwargs: dict[str, str] = {
            "public_key": _public_key(),
            "secret_key": _secret_key(),
        }
        host = _host()
        if host:
            kwargs["host"] = host
        _langfuse = Langfuse(**kwargs)
    return _langfuse


def start_generation(**kwargs: Any) -> Any:
    client = get_langfuse()
    if client is None:
        return NoopGeneration()
    return client.generation(**kwargs)


def configure_openai_langfuse() -> None:
    if not langfuse_configured():
        return

    import openai

    setattr(openai, "langfuse_public_key", _public_key())
    setattr(openai, "langfuse_secret_key", _secret_key())
    setattr(openai, "langfuse_host", _host())
    setattr(openai, "langfuse_enabled", True)


def build_async_openai_client(**kwargs: Any) -> Any:
    if langfuse_configured():
        from langfuse.openai import AsyncOpenAI

        configure_openai_langfuse()
    else:
        from openai import AsyncOpenAI

    return AsyncOpenAI(**kwargs)


def shutdown_langfuse() -> None:
    global _langfuse
    if _langfuse is None:
        return
    _langfuse.shutdown()
    _langfuse = None
