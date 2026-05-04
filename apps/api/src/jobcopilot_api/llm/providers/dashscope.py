"""DashscopeProvider — DashScope OpenAI-compatible endpoint (ADR-0003).

Endpoint: `https://dashscope.aliyuncs.com/compatible-mode/v1`
SDK:      `openai` AsyncOpenAI (`base_url` + `api_key` swap, no DashScope SDK)

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
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from jobcopilot_api.llm.client import Provider, ProviderRequest, ProviderResponse
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


class DashscopeProvider(Provider):
    def __init__(self, *, api_key: str, base_url: str = DASHSCOPE_BASE_URL) -> None:
        if not api_key:
            raise ValueError("DashscopeProvider requires a non-empty api_key")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "timeout": request.timeout_s,
            "max_tokens": request.max_tokens,
            "extra_body": {"enable_thinking": request.thinking_mode},
        }
        if request.response_format is not None:
            kwargs["response_format"] = request.response_format

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
            return ProviderResponse(content=content, tokens_in=0, tokens_out=0, cached_tokens=0)
        return ProviderResponse(
            content=content,
            tokens_in=int(usage.prompt_tokens),
            tokens_out=int(usage.completion_tokens),
            cached_tokens=_read_cached_tokens(usage),
        )
