"""Unit tests for BaseLLMClient: retry + JSON repair + cost + logging."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import BaseModel
from tenacity.wait import wait_none

from jobcopilot_api.llm.client import (
    BaseLLMClient,
    LLMResult,
    MemoryCallLogger,
    OnTokenCallback,
    ProviderRequest,
    ProviderResponse,
)
from jobcopilot_api.llm.errors import (
    LLMAuthError,
    LLMSchemaInvalidError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from jobcopilot_api.llm import tiers
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
    assert result.model == "qwen3.8-flash"
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
    # Flash: 1M in @ 1.2 + 1M out @ 7.2 = 8.4 CNY
    assert result.cost_cny == Decimal("8.4")


async def test_cost_zero_for_unknown_model(monkeypatch) -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=100, tokens_out=50)
    client, _ = _client(dummy)

    # 所有 tier 现在都映射到 qwen3.8-flash,且它在价目表内,因此改为
    # 直接把该 tier 的模型换成一个不存在于价目表的 ID 来覆盖该分支。
    monkeypatch.setattr(
        "jobcopilot_api.llm.tiers._TIER_TABLE",
        {
            **tiers._TIER_TABLE,
            Tier.PREMIUM: replace(
                tiers._TIER_TABLE[Tier.PREMIUM], model="model-not-in-price-table"
            ),
        },
    )
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
    assert sent.model == "qwen3.8-flash"


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


async def test_default_max_tokens_uses_tier_config() -> None:
    """S11 dogfood bad case #1: profile 长简历输出曾被 DashScope 默认 4096
    截断 → schema_invalid。Tier 默认 max_tokens 必须传到 ProviderRequest,
    且 CHEAP/STANDARD = 8192,PREMIUM = 16384。"""
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    for tier, expected in [(Tier.CHEAP, 8192), (Tier.STANDARD, 8192), (Tier.PREMIUM, 16384)]:
        await client.complete(feature="jd_parse", tier=tier, system="s", user="u")
        assert dummy.calls[-1].max_tokens == expected, f"{tier} should use {expected}"


async def test_explicit_max_tokens_overrides_tier_default() -> None:
    dummy = DummyProvider()
    dummy.queue(content="ok", tokens_in=1, tokens_out=1)
    client, _ = _client(dummy)

    await client.complete(
        feature="jd_parse",
        tier=Tier.CHEAP,
        system="sys",
        user="usr",
        max_tokens=2048,
    )
    assert dummy.calls[0].max_tokens == 2048


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


async def test_max_concurrency_applies_backpressure() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0
            self.active = 0
            self.max_active = 0

        async def complete(
            self,
            request: ProviderRequest,
            *,
            on_token: OnTokenCallback | None = None,
        ) -> ProviderResponse:
            del request, on_token
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            await self.release.wait()
            self.active -= 1
            return ProviderResponse(
                content="ok",
                tokens_in=1,
                tokens_out=1,
                cached_tokens=0,
            )

    provider = BlockingProvider()
    client = BaseLLMClient(provider=provider, max_concurrency=1)
    first = asyncio.create_task(
        client.complete(feature="first", tier=Tier.CHEAP, user="one")
    )
    await provider.started.wait()
    second = asyncio.create_task(
        client.complete(feature="second", tier=Tier.CHEAP, user="two")
    )
    await asyncio.sleep(0)

    assert provider.calls == 1
    provider.release.set()
    await asyncio.gather(first, second)
    assert provider.max_active == 1
