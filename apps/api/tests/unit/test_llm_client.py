"""Unit tests for BaseLLMClient: retry + JSON repair + cost + logging."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel
from tenacity.wait import wait_none

from jobcopilot_api.llm.client import (
    BaseLLMClient,
    LLMResult,
    MemoryCallLogger,
    ProviderRequest,
)
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.llm.tiers import Tier


class JobInfo(BaseModel):
    title: str
    company: str


def _client(provider: DummyProvider) -> tuple[BaseLLMClient, MemoryCallLogger]:
    """Construct a client with retry-wait disabled so tests don't sleep."""
    logger = MemoryCallLogger()
    client = BaseLLMClient(provider=provider, logger=logger, retry_wait=wait_none())
    return client, logger


# ---------- happy paths ----------


async def test_complete_happy_path() -> None:
    dummy = DummyProvider()
    dummy.queue(content="hello", tokens_in=10, tokens_out=4, cached_tokens=2)
    client, logger = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )

    assert result.success is True
    assert result.content == "hello"
    assert result.parsed is None
    assert result.tokens_in == 10
    assert result.tokens_out == 4
    assert result.cached_tokens == 2
    assert result.feature == "jd_parse"
    assert result.tier is Tier.CHEAP
    assert result.model == "qwen3.6-flash"
    assert result.thinking_mode is False
    assert result.error_code is None
    assert len(logger.calls) == 1
    assert logger.calls[0] is result


async def test_complete_with_schema_first_try() -> None:
    dummy = DummyProvider()
    dummy.queue(
        content='{"title":"SWE","company":"Acme"}',
        tokens_in=20,
        tokens_out=8,
    )
    client, logger = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        response_schema=JobInfo,
    )
    assert isinstance(result.parsed, JobInfo)
    assert result.parsed.title == "SWE"
    assert result.parsed.company == "Acme"
    assert len(logger.calls) == 1
    # response_format flows down to the provider on the first attempt.
    assert dummy.calls[0].response_format == {"type": "json_object"}


# ---------- schema repair ----------


async def test_complete_with_schema_retry_succeeds() -> None:
    dummy = DummyProvider()
    dummy.queue(content="not-json", tokens_in=20, tokens_out=4)
    dummy.queue(
        content='{"title":"SWE","company":"Acme"}',
        tokens_in=30,
        tokens_out=10,
    )
    client, logger = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        response_schema=JobInfo,
    )

    assert isinstance(result.parsed, JobInfo)
    # Tokens accumulate across the schema-retry call.
    assert result.tokens_in == 50
    assert result.tokens_out == 14
    # And the logger still only sees ONE row (ADR-0004 D4).
    assert len(logger.calls) == 1
    # The retry user prompt embeds the schema reminder + bad content.
    retry_user = dummy.calls[1].user
    assert "previous response was not valid JSON" in retry_user
    assert "not-json" in retry_user


async def test_complete_with_schema_retry_exhausted_raises() -> None:
    dummy = DummyProvider()
    dummy.queue(content="still-not-json", tokens_in=20, tokens_out=4)
    dummy.queue(content="also-not-json", tokens_in=30, tokens_out=10)
    client, logger = _client(dummy)

    with pytest.raises(LLMSchemaInvalidError):
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
            response_schema=JobInfo,
        )

    # Failure path still logs exactly one row, with the cost of both attempts.
    assert len(logger.calls) == 1
    failed = logger.calls[0]
    assert failed.success is False
    assert failed.error_code == "schema_invalid"
    assert failed.tokens_in == 50  # both attempts billed
    assert failed.tokens_out == 14


# ---------- tenacity retry on timeout / upstream ----------


async def test_complete_retries_on_timeout_then_succeeds() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMTimeoutError("first"))
    dummy.queue_error(LLMTimeoutError("second"))
    dummy.queue(content="ok", tokens_in=5, tokens_out=2)
    client, logger = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )
    assert result.success is True
    assert result.content == "ok"
    # All three attempts hit the provider.
    assert len(dummy.calls) == 3
    assert len(logger.calls) == 1


async def test_complete_retries_exhausted_raises_timeout() -> None:
    dummy = DummyProvider()
    for _ in range(3):
        dummy.queue_error(LLMTimeoutError("nope"))
    client, logger = _client(dummy)

    with pytest.raises(LLMTimeoutError):
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
        )

    assert len(dummy.calls) == 3  # exhausted
    assert len(logger.calls) == 1
    failed = logger.calls[0]
    assert failed.success is False
    assert failed.error_code == "timeout"
    # ADR-0004 D5 implementation rule: failed cost is 0, tokens are 0
    assert failed.cost_cny == Decimal("0")
    assert failed.tokens_in == 0
    assert failed.tokens_out == 0


async def test_complete_retries_upstream_5xx_then_succeeds() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMUpstreamError("502", status_code=502))
    dummy.queue_error(LLMUpstreamError("500", status_code=500))
    dummy.queue(content="ok", tokens_in=5, tokens_out=2)
    client, logger = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )
    assert result.success is True
    assert len(logger.calls) == 1


async def test_complete_does_not_retry_auth_error() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMAuthError("bad key"))
    client, logger = _client(dummy)

    with pytest.raises(LLMAuthError):
        await client.complete(
            feature="jd_parse",
            tier=Tier.CHEAP,
            system="sys",
            user="usr",
        )

    # Single attempt (no retry) and a single log row.
    assert len(dummy.calls) == 1
    assert len(logger.calls) == 1
    assert logger.calls[0].error_code == "auth"


# ---------- cost / metadata ----------


async def test_cost_uses_pricing_table_for_known_model() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1_000_000, tokens_out=1_000_000)
    client, _ = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )
    # Flash: 1M in @ 0.6 + 1M out @ 7.2 = 7.8 CNY
    assert result.cost_cny == Decimal("7.8")


async def test_cost_zero_for_unknown_model() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=100, tokens_out=50)
    client, _ = _client(dummy)

    # PREMIUM maps to qwen3.6-plus which is not yet in the price table.
    result = await client.complete(
        feature="jd_parse",
        tier=Tier.PREMIUM,
        system="sys",
        user="usr",
    )
    assert result.cost_cny == Decimal("0")
    assert result.success is True  # missing price ≠ failure


async def test_metadata_propagates_to_result() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    result = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        user_id=42,
        trace_id="trace-xyz",
        related_entity="jd",
        related_id=7,
        prompt_version_id=11,
    )
    assert result.user_id == 42
    assert result.trace_id == "trace-xyz"
    assert result.related_entity == "jd"
    assert result.related_id == 7
    assert result.prompt_version_id == 11


async def test_thinking_mode_for_standard_tier() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    await client.complete(
        feature="jd_parse",
        tier=Tier.STANDARD,
        system="sys",
        user="usr",
    )
    sent: ProviderRequest = dummy.calls[0]
    assert sent.thinking_mode is True
    assert sent.model == "qwen3.6-flash"


async def test_explicit_timeout_overrides_tier_default() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        timeout_s=5.0,
    )
    assert dummy.calls[0].timeout_s == 5.0


async def test_default_timeout_uses_tier_config() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    await client.complete(
        feature="jd_parse",
        tier=Tier.PREMIUM,
        system="sys",
        user="usr",
    )
    assert dummy.calls[0].timeout_s == 60.0


async def test_no_response_format_when_no_schema() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )
    assert dummy.calls[0].response_format is None


async def test_logger_default_is_noop_and_does_not_blow_up() -> None:
    """No logger argument -> NoopCallLogger, complete still returns."""
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client = BaseLLMClient(provider=dummy, retry_wait=wait_none())

    result: LLMResult = await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
    )
    assert result.success is True
