"""Embedder 批量调用(M1)— 走百炼 text-embedding-v4。

config(docs/TECH_DESIGN.md):
- model: text-embedding-v4(1024 维)
- thinking: N/A(非聊天模型,不走 prompt)
- 不走 prompt_versions 表

底层:infra/embedder.py 单例 DashscopeEmbedder + langfuse.openai wrapper
自动 instrument(LLM call / cost / cache hit / latency 进 Langfuse trace)。
错误 + retry(LLMTimeoutError / LLMUpstreamError exp backoff 3 次)由
DashscopeEmbedder 兜底。

调用方:workers/embed_worker(笔记入库后异步补)+ services/search_service
(hybrid_search 算 query 向量)。
"""

from __future__ import annotations

from decimal import Decimal
from time import monotonic

from jobcopilot_api.infra.embedder import get_embedder
from jobcopilot_api.llm import breaker
from jobcopilot_api.llm.admission import get_llm_admission_gate
from jobcopilot_api.llm.embedders import EMBED_BATCH_LIMIT, EmbeddingResult
from jobcopilot_api.llm.errors import LLMUpstreamError
from jobcopilot_api.llm.usage import FEATURE_EMBEDDING, record_usage
from jobcopilot_api.services import billing_service

# 落 note_chunks.embed_version 用;agent 内部规则升级时 bump
EMBED_VERSION = "v1"


async def embed_batch(
    texts: list[str],
    *,
    user_id: int | None = None,
) -> EmbeddingResult:
    """批量 embed,空入参直接返回空结果(避开网络请求 + cost)。

    单批 ≤ EMBED_BATCH_LIMIT(10);上层 worker / search 调用前应预切。

    P1:调用前查余额,调用后落 `llm_calls` 并按 `cost_cny` 实扣
    (`user_id=None` 表示无归属调用,不计费也不拦截)。
    """
    if not texts:
        return EmbeddingResult(
            vectors=[],
            tokens_in=0,
            model="",
            cost_cny=Decimal("0"),
        )
    if len(texts) > EMBED_BATCH_LIMIT:
        raise ValueError(
            f"embed_batch size {len(texts)} > {EMBED_BATCH_LIMIT}; 上层应预切"
        )
    await billing_service.assert_can_spend(user_id)
    breaker.check()
    embedder = get_embedder()
    started = monotonic()
    try:
        async with get_llm_admission_gate():
            result = await embedder.embed(texts)
    except LLMUpstreamError as exc:
        if exc.upstream_status_code == 429:
            breaker.record_rate_limited()
        await record_usage(
            user_id=user_id,
            feature=FEATURE_EMBEDDING,
            channel=billing_service.CHANNEL_EMBEDDING,
            model=embedder.model,
            tokens_in=0,
            cost_cny=Decimal("0"),
            latency_ms=int((monotonic() - started) * 1000),
            success=False,
            error_code=f"upstream_{exc.upstream_status_code}",
            metadata={"batch_size": len(texts)},
        )
        raise
    breaker.record_success()
    await record_usage(
        user_id=user_id,
        feature=FEATURE_EMBEDDING,
        channel=billing_service.CHANNEL_EMBEDDING,
        model=result.model,
        tokens_in=result.tokens_in,
        cost_cny=result.cost_cny,
        latency_ms=int((monotonic() - started) * 1000),
        success=True,
        metadata={"batch_size": len(texts)},
    )
    return result
