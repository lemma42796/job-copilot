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

P1 / P2 / P7 补齐(原实现绕过全部管控):
- 走 `llm/admission.py` 的进程内并发闸门,与文本生成共用一个上限。
- 调用前查余额,调用后经 `llm/usage.py` 落 `llm_calls` 并按 `cost_cny` 实扣。
- 上游 429 计入 `llm/breaker.py` 的熔断计数。
- 候选数不超过 top_k 时直接跳过(P6):这时精排不改变结果集,只花钱。

工程边界:
- **不走 OpenAI SDK**:rerank 不在 OpenAI 协议标准里,langfuse.openai 也不
  patch — 这里直接 httpx + 手动 generation 包;无 Langfuse key 时不构造
  SDK client,避免 CLI 评测脚本退出时被 noop background thread 拖住。
- **失败回退**:不抛错,降级为 hybrid 顺序前 top_k(rerank_score=0.0),
  trace 打 warning(docs/TECH_DESIGN.md)
- **relevance_score 不可跨请求比较**:仅本次请求内相对值,不存 DB,
  只在 pipeline 内部排序使用(memory §4)

调用方:services/retrieval_pipeline.py(M2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Any

import httpx

from jobcopilot_api.infra.langfuse import start_generation
from jobcopilot_api.llm import breaker
from jobcopilot_api.llm.admission import get_llm_admission_gate
from jobcopilot_api.llm.usage import FEATURE_RERANK, record_usage
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.services import billing_service
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
    user_id: int | None = None,
) -> RerankResult:
    """对 hybrid 召回的 chunks 跑 cross-encoder 精排。

    chunks 顺序假设是 hybrid RRF 后的优先级(失败回退时直接截前 top_k)。
    `user_id` 用于扣费归属;None 表示评测脚本等无归属调用,不计费。
    """
    if not chunks:
        return RerankResult(
            scored=[], total_tokens=0, model=model, cost_cny=Decimal("0")
        )

    # P6:候选不超过 top_k 时,精排排完还是这一组,顺序变化对下游取全量
    # context 无影响 —— 直接跳过,省一次上游调用。
    if settings.rerank_skip_when_candidates_le_top_k and len(chunks) <= top_k:
        logger.debug(
            "rerank skipped: %d candidates <= top_k %d", len(chunks), top_k
        )
        return RerankResult(
            scored=[(chunk, 0.0) for chunk in chunks],
            total_tokens=0,
            model=model,
            cost_cny=Decimal("0"),
        )

    candidate_chunks = chunks[:QWEN3_RERANK_MAX_DOCUMENTS]
    documents = [_format_document(c) for c in candidate_chunks]

    # P8 压测:上游换 stub 假实现 —— 固定延迟 / 固定并发上限,零真实调用。
    # 记账仍走 record_usage(模拟 token / 成本),保持账本链路满负载。
    if settings.llm_provider == "stub":
        from jobcopilot_api.llm.providers.stub import stub_upstream_call

        await billing_service.assert_can_spend(user_id)
        breaker.check()
        started = monotonic()
        async with get_llm_admission_gate():
            await stub_upstream_call()
        breaker.record_success()
        stub_tokens = len(query) + sum(len(d) for d in documents)
        stub_cost = _rerank_cost(stub_tokens)
        await record_usage(
            user_id=user_id,
            feature=FEATURE_RERANK,
            channel=billing_service.CHANNEL_RERANK,
            model=model,
            tokens_in=stub_tokens,
            cost_cny=stub_cost,
            latency_ms=int((monotonic() - started) * 1000),
            success=True,
            metadata={"doc_count": len(documents), "top_k": top_k, "stub": True},
        )
        scored = [
            (chunk, 1.0 - 0.01 * i)
            for i, chunk in enumerate(candidate_chunks[:top_k])
        ]
        return RerankResult(
            scored=scored,
            total_tokens=stub_tokens,
            model=model,
            cost_cny=stub_cost,
        )

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
    # P1:调用前查余额。余额不足直接向上抛,由 worker 映射成 job 终态。
    await billing_service.assert_can_spend(user_id)
    breaker.check()
    started = monotonic()
    try:
        client = _get_http_client()
        # P2:与文本生成共用同一个进程内并发闸门。
        async with get_llm_admission_gate():
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
        if resp.status_code == 429:
            breaker.record_rate_limited()
        resp.raise_for_status()
        body = resp.json()
        breaker.record_success()
    except (httpx.HTTPError, ValueError) as exc:
        generation.end(level="ERROR", status_message=f"rerank_failed: {exc}")
        logger.warning("rerank failed, falling back to hybrid order: %s", exc)
        await record_usage(
            user_id=user_id,
            feature=FEATURE_RERANK,
            channel=billing_service.CHANNEL_RERANK,
            model=model,
            tokens_in=0,
            cost_cny=Decimal("0"),
            latency_ms=int((monotonic() - started) * 1000),
            success=False,
            error_code="rerank_failed",
            metadata={"doc_count": len(documents)},
        )
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
    # P1:rerank 落 llm_calls 并实扣。这条链路此前既不落账也不过闸门。
    await record_usage(
        user_id=user_id,
        feature=FEATURE_RERANK,
        channel=billing_service.CHANNEL_RERANK,
        model=model,
        tokens_in=total_tokens,
        cost_cny=cost,
        latency_ms=int((monotonic() - started) * 1000),
        success=True,
        metadata={"doc_count": len(documents), "top_k": top_k},
    )
    return RerankResult(
        scored=scored,
        total_tokens=total_tokens,
        model=model,
        cost_cny=cost,
    )
