"""Unit tests for `agents.profile_parser.parse_profile`.

Pure function over LLMClient — DummyProvider replays a fixture, then we
assert the wire shape of the LLMResult that flows back to the service
layer (which is responsible for the parse_failed mapping).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tenacity.wait import wait_none

from jobcopilot_api.agents.profile_parser.agent import FEATURE, RELATED_ENTITY, parse_profile
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import BaseLLMClient, MemoryCallLogger
from jobcopilot_api.llm.errors import LLMSchemaInvalidError, LLMUpstreamError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.profiles import ProfileStructured

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"


def _prompt(prompt_id: int = 99) -> LoadedPrompt:
    return LoadedPrompt(
        id=prompt_id,
        agent_name="profile_parser",
        version="v1.0.0",
        system="you are a resume parser",
        user_template="parse: {{ resume_text }}",
        template_hash="x" * 64,
    )


def _client(provider: DummyProvider) -> tuple[BaseLLMClient, MemoryCallLogger]:
    logger = MemoryCallLogger()
    return (
        BaseLLMClient(provider=provider, logger=logger, retry_wait=wait_none()),
        logger,
    )


async def test_parse_profile_golden_returns_structured() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "profile_parser__golden.json")
    client, _ = _client(dummy)

    result = await parse_profile(
        text="张三 5 年后端工程师 (some long resume)",
        prompt=_prompt(prompt_id=42),
        llm=client,
        user_id=7,
        related_id=123,
    )

    assert result.success is True
    assert isinstance(result.parsed, ProfileStructured)
    assert result.parsed.full_name == "张三"
    assert result.parsed.email == "zhangsan@example.com"
    assert len(result.parsed.experiences) == 1
    assert result.parsed.experiences[0].company == "ACME"
    assert any(s.name == "python" for s in result.parsed.skills)
    assert result.parsed.educations[0].school == "上海交通大学"


async def test_parse_profile_propagates_metadata_to_result() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "profile_parser__golden.json")
    client, _ = _client(dummy)

    result = await parse_profile(
        text="some resume",
        prompt=_prompt(prompt_id=777),
        llm=client,
        user_id=1,
        related_id=10,
    )

    assert result.prompt_version_id == 777
    assert result.feature == FEATURE == "profile_parse"
    assert result.related_entity == RELATED_ENTITY == "profile"
    assert result.related_id == 10
    assert result.user_id == 1
    assert result.tier is Tier.CHEAP


async def test_parse_profile_propagates_upstream_error() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    client, logger = _client(dummy)

    with pytest.raises(LLMUpstreamError):
        await parse_profile(text="x" * 200, prompt=_prompt(), llm=client)

    assert len(logger.calls) == 1
    assert logger.calls[0].success is False


async def test_parse_profile_schema_invalid_after_retry() -> None:
    dummy = DummyProvider()
    dummy.queue(content="not json at all", tokens_in=20, tokens_out=4)
    dummy.queue(content="still not json", tokens_in=20, tokens_out=4)
    client, logger = _client(dummy)

    with pytest.raises(LLMSchemaInvalidError):
        await parse_profile(text="x" * 200, prompt=_prompt(), llm=client)

    assert len(logger.calls) == 1
    assert logger.calls[0].error_code == "schema_invalid"


async def test_parse_profile_renders_user_template_with_resume_text() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "profile_parser__golden.json")
    client, _ = _client(dummy)

    await parse_profile(text="MARKER_RESUME_99", prompt=_prompt(), llm=client)

    assert len(dummy.calls) >= 1
    first_user = dummy.calls[0].user
    assert "MARKER_RESUME_99" in first_user
    assert "{{ resume_text }}" not in first_user
    assert dummy.calls[0].system == "you are a resume parser"
