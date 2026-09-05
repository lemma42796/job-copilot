"""Unit tests for jobcopilot_api.llm.providers.dummy."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobcopilot_api.llm.client import ProviderRequest
from jobcopilot_api.llm.errors import LLMTimeoutError
from jobcopilot_api.llm.providers.dummy import DummyProvider, DummyScript

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"


def _request(content_marker: str = "u") -> ProviderRequest:
    return ProviderRequest(
        model="qwen3.8-flash",
        system="s",
        user=content_marker,
        response_format=None,
        thinking_mode=False,
        timeout_s=1.0,
    )


async def test_queue_returns_responses_in_order() -> None:
    dummy = DummyProvider()
    dummy.queue(content="first", tokens_in=1, tokens_out=2)
    dummy.queue(content="second", tokens_in=3, tokens_out=4)

    r1 = await dummy.complete(_request())
    r2 = await dummy.complete(_request())
    assert r1.content == "first"
    assert r1.tokens_in == 1
    assert r2.content == "second"
    assert r2.tokens_out == 4
    assert len(dummy.calls) == 2


async def test_queue_error_raises() -> None:
    dummy = DummyProvider()
    dummy.queue_error(LLMTimeoutError("simulated"))
    with pytest.raises(LLMTimeoutError):
        await dummy.complete(_request())


async def test_exhausted_queue_raises_loudly() -> None:
    dummy = DummyProvider()
    with pytest.raises(RuntimeError, match="queue exhausted"):
        await dummy.complete(_request())


async def test_queue_script_routes_to_response_or_error() -> None:
    dummy = DummyProvider()
    dummy.queue_script(DummyScript(content="ok", tokens_in=5))
    dummy.queue_script(DummyScript(error=LLMTimeoutError("x")))

    r = await dummy.complete(_request())
    assert r.content == "ok"
    with pytest.raises(LLMTimeoutError):
        await dummy.complete(_request())


async def test_from_fixture_loads_json() -> None:
    dummy = DummyProvider.from_fixture(FIXTURES / "jd_parse__happy_path.json")
    resp = await dummy.complete(_request())
    assert resp.tokens_in == 120
    assert resp.tokens_out == 18
    assert resp.cached_tokens == 0
    assert "Senior Backend Engineer" in resp.content
