"""Reranker service — retrieval pipeline 第三段(M2)。

百炼 `qwen3-rerank`(DashScope docs / console, 2026-05-13):
- 端点:`POST https://dashscope.aliyuncs.com/compatible-api/v1/reranks`
  (注意是 `compatible-api`,跟 chat 走的 `compatible-mode` 不通用)
- 请求体是扁平结构:`model/query/documents/top_n/instruct/return_documents`;
  不走 `input` / `parameters` 包裹(qwen3-vl-rerank / gte-rerank-v2 才走
  `/api/v1/services/rerank/text-rerank/text-rerank` + 嵌套结构)
- 限制:qwen3-rerank 单次最多 500 docs;每个 Query 或 Document 最多
  4,000 tokens;单请求最大 120,000 tokens;支持 100+ 语言
- 价格:¥0.0005/千 input tokens
- 计费公式:`Query Tokens × Document 数量 + Document Tokens 总和`
  (response.usage.total_tokens 已按此公式给值)
- `gte-rerank-v2` 将于 2026-05-30 下线;新实现只用 qwen3-rerank

工程边界:
- **不走 OpenAI SDK**:rerank 不在 OpenAI 协议标准里,langfuse.openai 也不
  patch — 这里直接 httpx + 手动 generation 包;无 Langfuse key 时不构造
  SDK client,避免 CLI 评测脚本退出时被 noop background thread 拖住。
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

from jobcopilot_api.infra.langfuse import start_generation
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.settings import settings

logger = logging.getLogger(__name__)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
RERANK_PATH = "/compatible-api/v1/reranks"
DEFAULT_MODEL = "qwen3-rerank"
DEFAULT_TOP_K = 10
DEFAULT_TIMEOUT_S = 30.0
QWEN3_RERANK_MAX_DOCUMENTS = 500
QWEN3_RERANK_MAX_TOKENS_PER_ITEM = 4000
QWEN3_RERANK_MAX_REQUEST_TOKENS = 120000
QWEN3_VL_RERANK_TEXT_MAX_DOCUMENTS = 100
QWEN3_VL_RERANK_IMAGE_MAX_DOCUMENTS = 40
QWEN3_VL_RERANK_VIDEO_MAX_DOCUMENTS = 4
QWEN3_VL_RERANK_MAX_TOKENS_PER_ITEM = 8000
QWEN3_VL_RERANK_MAX_REQUEST_TOKENS = 120000
GTE_RERANK_V2_DEPRECATION_DATE = "2026-05-30"

# direct-evidence 检索 instruct:不要只因路径 / 标题相似就把近邻材料抬高。
DEFAULT_INSTRUCT = (
    "Rank note chunks by direct evidence in CONTENT for answering the question. "
    "Prefer concrete facts, decisions, and constraints. Use source context only "
    "as a tie-breaker; do not reward folder, heading, interview question, "
    "summary, or related topic match alone."
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


def _format_document(chunk: NoteChunk) -> str:
    """正文优先;路径 / 标题只作为弱 source context。"""
    folder = " / ".join(chunk.folder_path) if chunk.folder_path else "<root>"
    heading = " > ".join(chunk.heading_path) if chunk.heading_path else "<root>"
    return (
        "Content:\n"
        f"{chunk.content}\n\n"
        "Source context (tie-breaker only):\n"
        f"Folder path: {folder}\n"
        f"Heading path: {heading}"
    )


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

    candidate_chunks = chunks[:QWEN3_RERANK_MAX_DOCUMENTS]
    documents = [_format_document(c) for c in candidate_chunks]
    generation = start_generation(
        name="reranker",
        model=model,
        input={"query": query, "doc_count": len(documents)},
        metadata={
            "top_k": top_k,
            "instruct": instruct,
            "document_format": "content + weak_source_context",
        },
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
        fallback = [(c, 0.0) for c in candidate_chunks[:top_k]]
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
        if idx is None or not (0 <= idx < len(candidate_chunks)):
            continue
        scored.append((candidate_chunks[idx], score))

    generation.end(
        output=f"{len(scored)} reranked",
        usage={
            "input": total_tokens,
            "output": 0,
            "total": total_tokens,
            "unit": "TOKENS",
        },
        metadata={
            "cost_cny": str(cost),
        },
    )
    return RerankResult(
        scored=scored,
        total_tokens=total_tokens,
        model=model,
        cost_cny=cost,
    )
