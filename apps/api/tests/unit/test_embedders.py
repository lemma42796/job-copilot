"""Unit tests for `llm.embedders` (S8 C2)。

DummyEmbedder 跑全:确定性、L2 归一化、batch 上限、空输入。
DashscopeEmbedder 用 AsyncMock 注入伪 client,只验:① wire kwargs
(`model` / `dimensions` / `encoding_format` / `input`)② 响应解码
③ 错误映射(SDK 错误 → LLM* 错误)④ tenacity 在 `LLMTimeoutError`
上重试 ⑤ batch ≤ 10 / 空输入 短路不打 API。
"""

from __future__ import annotations

import math
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
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
from tenacity.wait import wait_none

from jobcopilot_api.llm.embedders import (
    DEFAULT_EMBED_MODEL,
    EMBED_BATCH_LIMIT,
    EMBED_DIMENSIONS,
    DashscopeEmbedder,
    DummyEmbedder,
    EmbeddingResult,
    embed_cost_for,
)
from jobcopilot_api.llm.errors import LLMAuthError, LLMTimeoutError, LLMUpstreamError

# ----- 测试辅助 -----


def _fake_response(vectors: list[list[float]], prompt_tokens: int = 0) -> SimpleNamespace:
    """Mimic `openai.types.CreateEmbeddingResponse`(只暴露 `.data` / `.usage`)。"""
    data = [
        SimpleNamespace(embedding=v, index=i, object="embedding") for i, v in enumerate(vectors)
    ]
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens)
    return SimpleNamespace(data=data, usage=usage, model=DEFAULT_EMBED_MODEL, object="list")


def _mock_client(*, response: object | None = None, side_effect: object | None = None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.embeddings.create = AsyncMock(side_effect=side_effect)
    else:
        client.embeddings.create = AsyncMock(return_value=response)
    return client


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")


def _api_status_error(status: int, body: dict[str, object]) -> APIStatusError:
    response = httpx.Response(status, request=_http_request(), json=body)
    return APIStatusError("boom", response=response, body=body)


# ============ DummyEmbedder ============


async def test_dummy_returns_unit_vectors_with_correct_dimension() -> None:
    embedder = DummyEmbedder()
    result = await embedder.embed(["abc", "def"])
    assert result.model == "dummy-embed-v0"
    assert len(result.vectors) == 2
    for v in result.vectors:
        assert len(v) == EMBED_DIMENSIONS
        norm = math.sqrt(sum(x * x for x in v))
        assert math.isclose(norm, 1.0, rel_tol=1e-9, abs_tol=1e-9)


async def test_dummy_is_deterministic() -> None:
    a = await DummyEmbedder().embed(["hello"])
    b = await DummyEmbedder().embed(["hello"])
    assert a.vectors == b.vectors


async def test_dummy_distinct_inputs_yield_distinct_vectors() -> None:
    result = await DummyEmbedder().embed(["a", "b", "c"])
    assert result.vectors[0] != result.vectors[1]
    assert result.vectors[1] != result.vectors[2]


async def test_dummy_empty_input_returns_empty_result_no_call_overhead() -> None:
    result = await DummyEmbedder().embed([])
    assert result == EmbeddingResult(
        vectors=[], tokens_in=0, model="dummy-embed-v0", cost_cny=Decimal("0")
    )


async def test_dummy_batch_overflow_raises() -> None:
    with pytest.raises(ValueError, match="batch size"):
        await DummyEmbedder().embed(["x"] * (EMBED_BATCH_LIMIT + 1))


async def test_dummy_custom_dimensions() -> None:
    result = await DummyEmbedder(dimensions=64).embed(["abc"])
    assert len(result.vectors[0]) == 64


async def test_dummy_token_count_is_sum_of_lengths() -> None:
    result = await DummyEmbedder().embed(["abc", "defgh"])
    assert result.tokens_in == 8


def test_dummy_exposes_model_and_dimensions() -> None:
    e = DummyEmbedder(model="custom-dummy", dimensions=128)
    assert e.model == "custom-dummy"
    assert e.dimensions == 128


def test_dashscope_exposes_model_and_dimensions() -> None:
    e = DashscopeEmbedder(api_key="x", client=MagicMock(), dimensions=512)
    assert e.model == "text-embedding-v4"
    assert e.dimensions == 512


# ============ DashscopeEmbedder happy path ============


async def test_dashscope_passes_canonical_kwargs_and_decodes_response() -> None:
    response = _fake_response([[0.1] * 1024, [0.2] * 1024], prompt_tokens=42)
    client = _mock_client(response=response)
    embedder = DashscopeEmbedder(api_key="x", client=client, retry_wait=wait_none())

    result = await embedder.embed(["hello", "world"])

    client.embeddings.create.assert_awaited_once()
    kwargs = client.embeddings.create.await_args.kwargs
    assert kwargs["model"] == "text-embedding-v4"
    assert kwargs["input"] == ["hello", "world"]
    assert kwargs["dimensions"] == 1024
    assert kwargs["encoding_format"] == "float"
    assert "timeout" in kwargs

    assert len(result.vectors) == 2
    assert result.vectors[0] == [0.1] * 1024
    assert result.tokens_in == 42
    assert result.model == "text-embedding-v4"
    # 0.0005 元/千 tokens, 42 tokens => 0.000021 元
    assert result.cost_cny == Decimal("42") * Decimal("0.5") / Decimal("1000000")


async def test_dashscope_empty_input_short_circuits_no_api_call() -> None:
    client = _mock_client(response=_fake_response([]))
    embedder = DashscopeEmbedder(api_key="x", client=client, retry_wait=wait_none())

    result = await embedder.embed([])

    assert result.vectors == []
    assert result.tokens_in == 0
    client.embeddings.create.assert_not_awaited()


async def test_dashscope_batch_overflow_raises_before_api_call() -> None:
    client = _mock_client(response=_fake_response([]))
    embedder = DashscopeEmbedder(api_key="x", client=client, retry_wait=wait_none())

    with pytest.raises(ValueError, match="batch size"):
        await embedder.embed(["x"] * (EMBED_BATCH_LIMIT + 1))
    client.embeddings.create.assert_not_awaited()


async def test_dashscope_constructor_rejects_empty_api_key_when_no_client() -> None:
    with pytest.raises(ValueError, match="api_key"):
        DashscopeEmbedder(api_key="")


async def test_dashscope_custom_dimensions_param_propagates() -> None:
    response = _fake_response([[0.0] * 256], prompt_tokens=5)
    client = _mock_client(response=response)
    embedder = DashscopeEmbedder(api_key="x", client=client, dimensions=256, retry_wait=wait_none())

    await embedder.embed(["x"])

    assert client.embeddings.create.await_args.kwargs["dimensions"] == 256


# ============ DashscopeEmbedder 错误映射 ============


async def test_dashscope_timeout_maps_to_llm_timeout_error() -> None:
    client = _mock_client(side_effect=APITimeoutError(_http_request()))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMTimeoutError):
        await embedder.embed(["x"])


async def test_dashscope_connection_error_maps_to_llm_timeout_error() -> None:
    client = _mock_client(side_effect=APIConnectionError(request=_http_request()))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMTimeoutError):
        await embedder.embed(["x"])


async def test_dashscope_429_maps_to_llm_upstream_error() -> None:
    rl_response = httpx.Response(429, request=_http_request(), json={"error": {"message": "rate"}})
    client = _mock_client(side_effect=RateLimitError("rate", response=rl_response, body={}))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMUpstreamError) as exc:
        await embedder.embed(["x"])
    assert exc.value.upstream_status_code == 429


async def test_dashscope_500_maps_to_llm_upstream_error() -> None:
    sv_response = httpx.Response(500, request=_http_request(), json={"error": {"message": "boom"}})
    client = _mock_client(side_effect=InternalServerError("boom", response=sv_response, body={}))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMUpstreamError) as exc:
        await embedder.embed(["x"])
    assert exc.value.upstream_status_code == 500


@pytest.mark.parametrize(
    ("exc_cls", "status"),
    [
        (AuthenticationError, 401),
        (PermissionDeniedError, 403),
        (BadRequestError, 400),
        (NotFoundError, 404),
        (UnprocessableEntityError, 422),
    ],
)
async def test_dashscope_4xx_subclasses_map_to_llm_auth_error(exc_cls: type, status: int) -> None:
    response = httpx.Response(status, request=_http_request(), json={"error": {"message": "no"}})
    client = _mock_client(side_effect=exc_cls("no", response=response, body={}))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMAuthError):
        await embedder.embed(["x"])


async def test_dashscope_generic_4xx_maps_to_auth_error() -> None:
    """非 SDK 子类化的 4xx(如 418)走 catch-all → LLMAuthError。"""
    client = _mock_client(side_effect=_api_status_error(418, {"error": {"message": "teapot"}}))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMAuthError):
        await embedder.embed(["x"])


async def test_dashscope_generic_5xx_maps_to_upstream_error() -> None:
    """SDK 没子类化的 5xx(如 503)走 catch-all → LLMUpstreamError。"""
    client = _mock_client(side_effect=_api_status_error(503, {"error": {"message": "down"}}))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=1, retry_wait=wait_none()
    )

    with pytest.raises(LLMUpstreamError) as exc:
        await embedder.embed(["x"])
    assert exc.value.upstream_status_code == 503


# ============ DashscopeEmbedder retry ============


async def test_dashscope_retries_on_timeout_then_succeeds() -> None:
    response = _fake_response([[0.5] * 1024], prompt_tokens=3)
    client = MagicMock()
    client.embeddings.create = AsyncMock(side_effect=[APITimeoutError(_http_request()), response])
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=3, retry_wait=wait_none()
    )

    result = await embedder.embed(["x"])

    assert client.embeddings.create.await_count == 2
    assert result.tokens_in == 3


async def test_dashscope_retries_exhaust_then_reraise() -> None:
    client = MagicMock()
    client.embeddings.create = AsyncMock(side_effect=APITimeoutError(_http_request()))
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=3, retry_wait=wait_none()
    )

    with pytest.raises(LLMTimeoutError):
        await embedder.embed(["x"])
    assert client.embeddings.create.await_count == 3


async def test_dashscope_does_not_retry_on_auth_error() -> None:
    response = httpx.Response(401, request=_http_request(), json={"error": {"message": "no"}})
    client = MagicMock()
    client.embeddings.create = AsyncMock(
        side_effect=AuthenticationError("no", response=response, body={})
    )
    embedder = DashscopeEmbedder(
        api_key="x", client=client, retry_attempts=3, retry_wait=wait_none()
    )

    with pytest.raises(LLMAuthError):
        await embedder.embed(["x"])
    assert client.embeddings.create.await_count == 1


# ============ pricing ============


def test_embed_cost_for_known_model() -> None:
    # 1000 tokens at 0.5 元/M => 0.0005 元
    assert embed_cost_for(model="text-embedding-v4", tokens_in=1000) == Decimal("0.0005")


def test_embed_cost_for_unknown_model_returns_zero() -> None:
    assert embed_cost_for(model="not-a-real-model", tokens_in=1000) == Decimal("0")


def test_embed_cost_for_zero_tokens() -> None:
    assert embed_cost_for(model="text-embedding-v4", tokens_in=0) == Decimal("0")
