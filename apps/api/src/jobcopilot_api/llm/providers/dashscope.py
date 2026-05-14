"""DashscopeProvider — DashScope OpenAI-compatible endpoint (ADR-0003).

Endpoint: `https://dashscope.aliyuncs.com/compatible-mode/v1`
SDK:      OpenAI-compatible AsyncOpenAI;有 Langfuse key 时才走
          `langfuse.openai` 自动 instrument,否则走原生 OpenAI client。
          错误类仍从 `openai` 导(langfuse 只 wrap 客户端类,不 wrap 错误)。

Error mapping (ADR-0004 D3):

  APITimeoutError / APIConnectionError      -> LLMTimeoutError (retryable)
  RateLimitError (429)                       -> LLMUpstreamError(429) (retryable)
  InternalServerError (>=500)                -> LLMUpstreamError(status) (retryable)
  AuthenticationError / PermissionDeniedError
    / BadRequestError / NotFoundError
    / UnprocessableEntityError               -> LLMAuthError (NOT retryable)

The thinking-mode toggle for Qwen3.6 rides on `extra_body={"enable_thinking": ...}`
in OpenAI-compat mode. If a future Qwen release moves it elsewhere, only this
file needs to change.
"""

from __future__ import annotations

from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from jobcopilot_api.infra.langfuse import build_async_openai_client
from jobcopilot_api.llm.client import (
    OnTokenCallback,
    Provider,
    ProviderRequest,
    ProviderResponse,
)
from jobcopilot_api.llm.errors import LLMAuthError, LLMTimeoutError, LLMUpstreamError

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _read_cached_tokens(usage: Any) -> int:
    """Pull `prompt_tokens_details.cached_tokens` defensively.

    The OpenAI SDK exposes this nested object as a Pydantic model when the
    provider includes it, but DashScope may omit it on cache misses. We
    read it as `Any` and fall back to 0 if absent.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    cached = getattr(details, "cached_tokens", None)
    if cached is None:
        return 0
    return int(cached)


def _read_cache_creation_input_tokens(usage: Any) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    created = getattr(details, "cache_creation_input_tokens", None)
    if created is None:
        return 0
    return int(created)


class DashscopeProvider(Provider):
    def __init__(self, *, api_key: str, base_url: str = DASHSCOPE_BASE_URL) -> None:
        if not api_key:
            raise ValueError("DashscopeProvider requires a non-empty api_key")
        self._client = build_async_openai_client(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        request: ProviderRequest,
        *,
        on_token: OnTokenCallback | None = None,
    ) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages
            if request.messages is not None
            else [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "timeout": request.timeout_s,
            "max_tokens": request.max_tokens,
            "extra_body": {"enable_thinking": request.thinking_mode},
        }
        if request.response_format is not None:
            kwargs["response_format"] = request.response_format
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if on_token is not None:
            return await self._complete_stream(kwargs, on_token=on_token)

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except (APITimeoutError, APIConnectionError) as e:
            raise LLMTimeoutError(str(e)) from e
        except RateLimitError as e:
            raise LLMUpstreamError(str(e), status_code=429) from e
        except InternalServerError as e:
            raise LLMUpstreamError(str(e), status_code=e.status_code or 500) from e
        except (
            AuthenticationError,
            PermissionDeniedError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as e:
            raise LLMAuthError(str(e)) from e
        except APIStatusError as e:
            # Catch-all for any other 4xx/5xx the SDK didn't sub-class above.
            status = e.status_code or 0
            if status >= 500 or status == 429:
                raise LLMUpstreamError(str(e), status_code=status) from e
            raise LLMAuthError(str(e)) from e

        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = resp.usage
        if usage is None:  # pragma: no cover - DashScope always returns usage
            return ProviderResponse(
                content=content,
                tokens_in=0,
                tokens_out=0,
                cached_tokens=0,
                cache_creation_input_tokens=0,
            )
        return ProviderResponse(
            content=content,
            tokens_in=int(usage.prompt_tokens),
            tokens_out=int(usage.completion_tokens),
            cached_tokens=_read_cached_tokens(usage),
            cache_creation_input_tokens=_read_cache_creation_input_tokens(usage),
        )

    async def _complete_stream(
        self,
        kwargs: dict[str, Any],
        *,
        on_token: OnTokenCallback,
    ) -> ProviderResponse:
        """Streaming variant: emit deltas via callback while accumulating
        the full content + usage to return.

        DashScope OpenAI-compat 实测 `stream_options={"include_usage": True}`
        在最末一帧(`choices=[]` 的 sentinel chunk)给出完整 usage,与 OpenAI
        语义对齐;这里依赖此行为,若 provider 不返回 usage 则降级到 0
        (与非流式 `usage is None` 分支一致)。"""
        stream_kwargs = {**kwargs, "stream": True, "stream_options": {"include_usage": True}}
        content_parts: list[str] = []
        tokens_in = 0
        tokens_out = 0
        cached_tokens = 0
        cache_creation_input_tokens = 0
        try:
            stream = await self._client.chat.completions.create(**stream_kwargs)
            async for chunk in stream:
                if chunk.usage is not None:
                    tokens_in = int(chunk.usage.prompt_tokens)
                    tokens_out = int(chunk.usage.completion_tokens)
                    cached_tokens = _read_cached_tokens(chunk.usage)
                    cache_creation_input_tokens = _read_cache_creation_input_tokens(
                        chunk.usage
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    await on_token(piece)
        except (APITimeoutError, APIConnectionError) as e:
            raise LLMTimeoutError(str(e)) from e
        except RateLimitError as e:
            raise LLMUpstreamError(str(e), status_code=429) from e
        except InternalServerError as e:
            raise LLMUpstreamError(str(e), status_code=e.status_code or 500) from e
        except (
            AuthenticationError,
            PermissionDeniedError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as e:
            raise LLMAuthError(str(e)) from e
        except APIStatusError as e:
            status = e.status_code or 0
            if status >= 500 or status == 429:
                raise LLMUpstreamError(str(e), status_code=status) from e
            raise LLMAuthError(str(e)) from e

        return ProviderResponse(
            content="".join(content_parts),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        )
