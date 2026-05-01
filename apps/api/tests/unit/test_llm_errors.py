"""Unit tests for jobcopilot_api.llm.errors."""

from __future__ import annotations

from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)


def test_llm_errors_inherit_jobcopilot_error() -> None:
    # All LLM-layer errors must surface through the global RFC 7807 handler.
    assert issubclass(LLMError, JobCopilotError)
    for cls in (LLMTimeoutError, LLMUpstreamError, LLMAuthError, LLMSchemaInvalidError):
        assert issubclass(cls, LLMError)


def test_timeout_status_code_is_504() -> None:
    err = LLMTimeoutError("simulated")
    assert err.status_code == 504
    assert err.code == "LLM_TIMEOUT"


def test_upstream_carries_origin_status_code() -> None:
    err = LLMUpstreamError("rate limited", status_code=429)
    assert err.upstream_status_code == 429
    assert err.status_code == 502  # what we expose to our own clients


def test_auth_error_is_not_retryable_by_marker() -> None:
    # Marker-style: tenacity policy in BaseLLMClient retries only on
    # LLMTimeoutError / LLMUpstreamError, so LLMAuthError instances must
    # be neither.
    err = LLMAuthError("bad key")
    assert not isinstance(err, LLMTimeoutError)
    assert not isinstance(err, LLMUpstreamError)


def test_schema_invalid_is_not_retryable_by_tenacity() -> None:
    err = LLMSchemaInvalidError("malformed json")
    assert not isinstance(err, LLMTimeoutError)
    assert not isinstance(err, LLMUpstreamError)
