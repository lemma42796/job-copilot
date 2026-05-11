"""Reranker service — retrieval pipeline 第三段(M2)。

百炼 `qwen3-rerank`(reference memory `reference_aliyun_dashscope_rerank.md`):
- 端点:`POST https://dashscope.aliyuncs.com/compatible-api/v1/reranks`
  (注意是 `compatible-api`,跟 chat 走的 `compatible-mode` 不通用)
- 价格:¥0.0005/千 token,500 doc 上限
- 计费公式:`Query Tokens × Document 数量 + Document Tokens 总和`
  (response.usage.total_tokens 已按此公式给值)

工程边界:
- **不走 OpenAI SDK**:rerank 不在 OpenAI 协议标准里,langfuse.openai 也不
  patch — 这里直接 httpx + 手动 `Langfuse().generation()` 包(参考 embedder
  在 STATUS 永久约束 "langfuse 2.x 的 langfuse.openai 不 patch embeddings.create")
- **失败回退**:不抛错,降级为 hybrid 顺序前 top_k(rerank_score=0.0),
  trace 打 warning(5-AGENT §2.7.5)
- **relevance_score 不可跨请求比较**:仅本次请求内相对值,不存 DB,
  只在 pipeline 内部排序使用(memory §4)

调用方:services/retrieval_pipeline.py(M2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from langfuse import Langfuse

from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.settings import settings

logger = logging.getLogger(__name__)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
RERANK_PATH = "/compatible-api/v1/reranks"
DEFAULT_MODEL = "qwen3-rerank"
DEFAULT_TOP_K = 10
DEFAULT_TIMEOUT_S = 30.0

# memory §5 默认问答检索 instruct(英文写,贴 "用户 query → 找笔记" 场景)
DEFAULT_INSTRUCT = (
    "Given a web search query, retrieve relevant passages that answer the query."
)

# memory §3 计费 + 单价
RERANK_PRICE_PER_1K = Decimal("0.0005")


@dataclass(frozen=True)
class RerankResult:
    """rerank 结果(chunk + score 配对,降序)。score 不可跨请求比较。"""

    scored: list[tuple[NoteChunk, float]]
    total_tokens: int
    model: str
    cost_cny: Decimal


_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        if not settings.dashscope_api_key:
            raise ValueError(
                "reranker requires non-empty JOBCOPILOT_DASHSCOPE_API_KEY"
            )
        _http_client = httpx.AsyncClient(
            base_url=DASHSCOPE_BASE,
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(DEFAULT_TIMEOUT_S),
        )
    return _http_client


async def reset_http_client() -> None:
    """Test helper / lifespan shutdown:释放 keep-alive 连接。"""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
    _http_client = None


def _rerank_cost(total_tokens: int) -> Decimal:
    return Decimal(total_tokens) * RERANK_PRICE_PER_1K / Decimal("1000")


async def rerank(
    query: str,
    chunks: list[NoteChunk],
    *,
    top_k: int = DEFAULT_TOP_K,
    instruct: str = DEFAULT_INSTRUCT,
    model: str = DEFAULT_MODEL,
) -> RerankResult:
    """对 hybrid 召回的 chunks 跑 cross-encoder 精排。

    chunks 顺序假设是 hybrid RRF 后的优先级(失败回退时直接截前 top_k)。
    """
    if not chunks:
        return RerankResult(
            scored=[], total_tokens=0, model=model, cost_cny=Decimal("0")
        )

    documents = [c.content for c in chunks]
    generation = Langfuse().generation(
        name="reranker",
        model=model,
        input={"query": query, "doc_count": len(documents)},
        metadata={"top_k": top_k, "instruct": instruct},
    )
    try:
        client = _get_http_client()
        resp = await client.post(
            RERANK_PATH,
            json={
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": min(top_k, len(documents)),
                "return_documents": False,
                "instruct": instruct,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        generation.end(level="ERROR", status_message=f"rerank_failed: {exc}")
        logger.warning("rerank failed, falling back to hybrid order: %s", exc)
        fallback = [(c, 0.0) for c in chunks[:top_k]]
        return RerankResult(
            scored=fallback,
            total_tokens=0,
            model=model,
            cost_cny=Decimal("0"),
        )

    results: list[dict[str, Any]] = body.get("results", [])
    total_tokens = int((body.get("usage") or {}).get("total_tokens", 0))
    cost = _rerank_cost(total_tokens)

    scored: list[tuple[NoteChunk, float]] = []
    for r in results:
        idx = r.get("index")
        score = float(r.get("relevance_score", 0.0))
        if idx is None or not (0 <= idx < len(chunks)):
            continue
        scored.append((chunks[idx], score))

    generation.end(
        output=f"{len(scored)} reranked",
        usage={
            "input": total_tokens,
            "output": 0,
            "total": total_tokens,
            "unit": "TOKENS",
        },
        metadata={"cost_cny": str(cost)},
    )
    return RerankResult(
        scored=scored,
        total_tokens=total_tokens,
        model=model,
        cost_cny=cost,
    )
