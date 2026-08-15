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

from jobcopilot_api.infra.embedder import get_embedder
from jobcopilot_api.llm.embedders import EMBED_BATCH_LIMIT, EmbeddingResult

# 落 note_chunks.embed_version 用;agent 内部规则升级时 bump
EMBED_VERSION = "v1"


async def embed_batch(texts: list[str]) -> EmbeddingResult:
    """批量 embed,空入参直接返回空结果(避开网络请求 + cost)。

    单批 ≤ EMBED_BATCH_LIMIT(10);上层 worker / search 调用前应预切。
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
    embedder = get_embedder()
    return await embedder.embed(texts)
