"""Unit tests for `agents.jd_parser.parse_jd`. ADR-0006 D6 / D8.

Pure function over LLMClient — DummyProvider replays a fixture, then we
assert the wire shape of the LLMResult that flows back to the service
layer (which is responsible for the parse_failed mapping).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tenacity.wait import wait_none

from jobcopilot_api.agents.jd_parser.agent import FEATURE, RELATED_ENTITY, parse_jd
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.llm.client import BaseLLMClient, MemoryCallLogger
from jobcopilot_api.llm.errors import LLMSchemaInvalidError, LLMUpstreamError
from jobcopilot_api.llm.providers.dummy import DummyProvider
from jobcopilot_api.llm.tiers import Tier
from jobcopilot_api.schemas.jds import JDStructured

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"


def _prompt(prompt_id: int = 99) -> LoadedPrompt:
    """A test prompt — minimal SYSTEM, USER with `{{ jd_text }}`."""
    return LoadedPrompt(
        id=prompt_id,
        agent_name="jd_parser",
        version="v1.0.0",
        system="you are a JD parser",
        user_template="parse: {{ jd_text }}",
        template_hash="x" * 64,
    )


def _client(provider: DummyProvider) -> tuple[BaseLLMClient, MemoryCallLogger]:
    logger = MemoryCallLogger()
    return (
        BaseLLMClient(provider=provider, logger=logger, retry_wait=wait_none()),
        logger,
    )


# ---- golden: fixture replay → JDStructured on result.parsed ----


async def test_parse_jd_golden_returns_jd_structured() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "jd_parser__golden.json")
    client, _logger = _client(dummy)

    result = await parse_jd(
        text="Senior Backend @ ACME (some long JD)",
        prompt=_prompt(prompt_id=42),
        llm=client,
        user_id=7,
        related_id=123,
    )

    assert result.success is True
    assert isinstance(result.parsed, JDStructured)
    assert result.parsed.title == "Senior Backend Engineer"
    assert result.parsed.company == "ACME"
    assert result.parsed.confidence == 0.92
    assert any(s.name == "python" for s in result.parsed.hard_skills)


# ---- propagates the prompt_version_id / feature / related_entity ----


async def test_parse_jd_propagates_prompt_version_id_to_result() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "jd_parser__golden.json")
    client, _ = _client(dummy)

    result = await parse_jd(
        text="some JD",
        prompt=_prompt(prompt_id=777),
        llm=client,
        user_id=1,
        related_id=10,
    )

    assert result.prompt_version_id == 777
    assert result.feature == FEATURE == "jd_parse"
    assert result.related_entity == RELATED_ENTITY == "jd"
    assert result.related_id == 10
    assert result.user_id == 1
    assert result.tier is Tier.CHEAP


# ---- LLM upstream error: bubbles up; logger gets a failed row ----


async def test_parse_jd_propagates_upstream_error() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    dummy.queue_error(LLMUpstreamError("boom", status_code=500))
    client, logger = _client(dummy)

    with pytest.raises(LLMUpstreamError):
        await parse_jd(text="x" * 100, prompt=_prompt(), llm=client)

    # Failure path still writes exactly one llm_calls row.
    assert len(logger.calls) == 1
    assert logger.calls[0].success is False
    assert logger.calls[0].error_code == "upstream_500"
    assert logger.calls[0].prompt_version_id == 99


# ---- Schema invalid (after 1 retry): raises LLMSchemaInvalidError ----


async def test_parse_jd_schema_invalid_after_retry() -> None:
    dummy = DummyProvider()
    # First attempt: not valid JSON -> triggers schema retry
    dummy.queue(content="not json at all", tokens_in=20, tokens_out=4)
    # Second attempt (retry with schema reminder): still bad
    dummy.queue(content="still not json", tokens_in=20, tokens_out=4)
    client, logger = _client(dummy)

    with pytest.raises(LLMSchemaInvalidError):
        await parse_jd(text="x" * 100, prompt=_prompt(), llm=client)

    assert len(logger.calls) == 1
    assert logger.calls[0].success is False
    assert logger.calls[0].error_code == "schema_invalid"


# ---- prompt rendering: jd_text reaches the user message verbatim ----


async def test_parse_jd_renders_user_template_with_jd_text() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "jd_parser__golden.json")
    client, _ = _client(dummy)

    await parse_jd(
        text="MARKER_TEXT_42",
        prompt=_prompt(),
        llm=client,
    )

    # The provider records the actual ProviderRequest sent. The user
    # message is augmented by BaseLLMClient with the JSON schema; we
    # only assert our `{{ jd_text }}` got substituted into it.
    assert len(dummy.calls) >= 1
    first_user = dummy.calls[0].user
    assert "MARKER_TEXT_42" in first_user
    assert "{{ jd_text }}" not in first_user
    # System is the constant from LoadedPrompt — never rendered.
    assert dummy.calls[0].system == "you are a JD parser"
