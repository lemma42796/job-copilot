"""Embedder Protocol + Dummy + Dashscope(S8 C2)。

文本 → 1024 维向量 + tokens_in。S8 阶段不写 `llm_calls`(M2 #12 把 schema
通用化时再统一);service 层(C4)结构化日志打印 token + cost。

错误沿用 S2 LLM* 体系(同 DashscopeProvider 的映射);L1 基础重试 = 同
LLMClient 的 3 次 exp backoff,只 retry `LLMTimeoutError` / `LLMUpstreamError`。

百炼 wire 契约:OpenAI 兼容端点 `/compatible-mode/v1/embeddings`,
请求 `model + input(str|list[str]) + dimensions + encoding_format`,
响应 `data[].embedding` + `usage.prompt_tokens`(无 completion_tokens)。
单行 ≤ 8192 tokens / 单 batch ≤ 10 行(STATUS.md S8 已锁)。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from langfuse.openai import AsyncOpenAI
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
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)
from tenacity.wait import wait_base

from jobcopilot_api.llm.errors import LLMAuthError, LLMTimeoutError, LLMUpstreamError
from jobcopilot_api.llm.providers.dashscope import DASHSCOPE_BASE_URL

# ---------- 常量 ----------

EMBED_DIMENSIONS = 1024
EMBED_BATCH_LIMIT = 10  # 百炼 v4 单 batch 上限
DEFAULT_EMBED_MODEL = "text-embedding-v4"
DEFAULT_DUMMY_MODEL = "dummy-embed-v0"

# 百炼 embedding 实时单价(CNY / 1M input tokens)。Batch 价是其一半,M2 接 batch 时再补。
EMBED_PRICE_PER_MILLION: dict[str, Decimal] = {
    "text-embedding-v4": Decimal("0.5"),  # 0.0005 元 / 千 tokens
    "text-embedding-v3": Decimal("0.7"),  # 0.0007 元 / 千 tokens
}


def embed_cost_for(*, model: str, tokens_in: int) -> Decimal:
    """CNY 成本。模型不在表中返回 0(同 chat pricing 的容错)。"""
    rate = EMBED_PRICE_PER_MILLION.get(model)
    if rate is None:
        return Decimal("0")
    return Decimal(tokens_in) * rate / Decimal("1000000")


# ---------- 结果 ----------


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]  # parallel to input texts
    tokens_in: int  # `usage.prompt_tokens`(embedding 无 completion_tokens)
    model: str
    cost_cny: Decimal


# ---------- Protocol ----------


class Embedder(Protocol):
    """Service 层只看这个;C4 通过它把 chunk 文本批量转成向量。"""

    @property
    def model(self) -> str: ...

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...


# ---------- Dummy ----------


def _hash_to_unit_vector(text: str, dim: int) -> list[float]:
    """sha256 链 → dim 个 float32-范围数 → L2 归一化到单位向量。

    确定性:同输入同向量,可在测试里 freeze。无碰撞保证(sha256 输出
    分布均匀,1024 维相同的概率 ≈ 0)。
    """
    seed = text.encode("utf-8")
    raw = b""
    counter = 0
    needed = dim * 4
    while len(raw) < needed:
        raw += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    raw = raw[:needed]

    floats = [
        int.from_bytes(raw[i * 4 : (i + 1) * 4], "big", signed=True) / (1 << 31) for i in range(dim)
    ]
    norm = math.sqrt(sum(x * x for x in floats))
    if norm == 0:  # pragma: no cover - 32 字节全零的概率 ≈ 0
        return floats
    return [x / norm for x in floats]


class DummyEmbedder:
    """Hash-based embedder for tests + offline runs(STATUS.md S8 已锁规则)。"""

    def __init__(
        self,
        *,
        model: str = DEFAULT_DUMMY_MODEL,
        dimensions: int = EMBED_DIMENSIONS,
    ) -> None:
        self._model = model
        self._dim = dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if len(texts) > EMBED_BATCH_LIMIT:
            raise ValueError(f"batch size {len(texts)} > {EMBED_BATCH_LIMIT}; chunk 上游应预切")
        if not texts:
            return EmbeddingResult(
                vectors=[], tokens_in=0, model=self._model, cost_cny=Decimal("0")
            )
        vectors = [_hash_to_unit_vector(t, self._dim) for t in texts]
        # 只是给 service 层一个 deterministic 数字,不是真 token 计数
        tokens = sum(len(t) for t in texts)
        return EmbeddingResult(
            vectors=vectors,
            tokens_in=tokens,
            model=self._model,
            cost_cny=Decimal("0"),
        )


# ---------- Dashscope ----------


DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_WAIT: wait_base = wait_exponential(multiplier=0.5, max=4) + wait_random(0, 0.5)


class DashscopeEmbedder:
    """百炼 OpenAI-compat embedding 客户端。

    错误映射、retry 配置与 `DashscopeProvider` 对齐,只是 endpoint
    走 `embeddings` 而非 `chat.completions`。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EMBED_MODEL,
        dimensions: int = EMBED_DIMENSIONS,
        base_url: str = DASHSCOPE_BASE_URL,
        timeout_s: float = 30.0,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_wait: wait_base = DEFAULT_RETRY_WAIT,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("DashscopeEmbedder requires a non-empty api_key")
        self._client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dim = dimensions
        self._timeout_s = timeout_s
        self._retry_attempts = retry_attempts
        self._retry_wait = retry_wait

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if len(texts) > EMBED_BATCH_LIMIT:
            raise ValueError(f"batch size {len(texts)} > {EMBED_BATCH_LIMIT}; chunk 上游应预切")
        if not texts:
            return EmbeddingResult(
                vectors=[], tokens_in=0, model=self._model, cost_cny=Decimal("0")
            )

        result: EmbeddingResult | None = None
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((LLMTimeoutError, LLMUpstreamError)),
            stop=stop_after_attempt(self._retry_attempts),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                result = await self._call(texts)
        assert result is not None  # tenacity reraise=True 保证执行到这里 result 已赋值
        return result

    async def _call(self, texts: list[str]) -> EmbeddingResult:
        try:
            resp = await self._client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dim,
                encoding_format="float",
                timeout=self._timeout_s,
            )
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

        vectors = [list(d.embedding) for d in resp.data]
        tokens_in = _read_prompt_tokens(resp.usage)
        return EmbeddingResult(
            vectors=vectors,
            tokens_in=tokens_in,
            model=self._model,
            cost_cny=embed_cost_for(model=self._model, tokens_in=tokens_in),
        )


def _read_prompt_tokens(usage: Any) -> int:
    """Embedding 响应只有 prompt_tokens / total_tokens(无 completion)。"""
    if usage is None:  # pragma: no cover - 百炼始终返回 usage
        return 0
    return int(usage.prompt_tokens)
