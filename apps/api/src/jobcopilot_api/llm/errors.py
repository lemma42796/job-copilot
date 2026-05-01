"""LLM error hierarchy (ADR-0004 §LLMClient 契约 / D3).

All errors inherit `JobCopilotError`, so they translate to RFC 7807 via the
exception handler installed in `main.py`.
"""

from __future__ import annotations

from jobcopilot_api.errors import JobCopilotError


class LLMError(JobCopilotError):
    """Base for all LLM-layer errors."""

    status_code = 502
    code = "LLM_ERROR"
    title = "LLM 调用失败"


class LLMTimeoutError(LLMError):
    """Client-side asyncio timeout. Retryable by tenacity."""

    status_code = 504
    code = "LLM_TIMEOUT"
    title = "LLM 调用超时"


class LLMUpstreamError(LLMError):
    """Provider returned a retryable status (429 / 5xx)."""

    status_code = 502
    code = "LLM_UPSTREAM"
    title = "LLM 上游服务异常"

    def __init__(self, detail: str = "", *, status_code: int) -> None:
        super().__init__(detail)
        self.upstream_status_code = status_code


class LLMAuthError(LLMError):
    """Provider rejected with a non-retryable 4xx (auth / quota / bad request)."""

    status_code = 502
    code = "LLM_AUTH"
    title = "LLM 调用未授权"


class LLMSchemaInvalidError(LLMError):
    """Provider returned content that does not parse against `response_schema`.

    Raised after the single retry attempt described in ADR-0004 (the LLMClient
    re-prompts once with the schema appended; if that still fails this fires).
    """

    status_code = 502
    code = "LLM_SCHEMA_INVALID"
    title = "LLM 返回不符合 schema"
