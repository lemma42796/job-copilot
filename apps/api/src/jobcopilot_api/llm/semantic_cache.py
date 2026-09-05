"""P6 语义缓存 —— 精确 key 未命中时的近似命中。

`llm/cache_store.py` 的精确命中要求请求文本一字不差。真实使用里同一个人
换个措辞问同一件事("讲讲 GIL" / "解释一下 GIL")会各打一次上游。近似命中
把请求文本 embed 成向量,在同一用户、同一 feature、同一 model 的历史缓存
里找余弦相似度 ≥ `llm_semantic_cache_min_similarity` 的最近邻。

三条硬约束:

1. **按用户隔离**。`llm_response_cache.request` 存的是用户原始输入(笔记
   片段、query),跨用户复用等于把 A 的笔记内容返回给 B。所有查询都带
   `user_id = :user_id`,`user_id IS NULL` 的系统调用不参与近似命中。
2. **成本审计不变**。近似命中和精确命中走同一条记账路径:`llm_calls` 照常
   落一行(cached=true、cost 记 0),`llm_response_cache.response` 里的
   tokens 原样保留,"本该花多少"仍然可以从缓存行重建。
3. **相似度阈值高**。默认 0.97 —— 近似命中返回的是**另一个请求**的答案,
   阈值低了就是答非所问。默认关闭(`llm_semantic_cache_enabled=false`),
   需要显式开。

embedding 调用本身要花钱,所以只在文本长度落在
`[_MIN_TEXT_CHARS, _MAX_TEXT_CHARS]` 时才做 —— 太短的请求区分度不够,
太长的(带整段 chunks 的 prompt)embed 成本接近直接调生成模型。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text

from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.llm.cache_store import CachedResponse
from jobcopilot_api.settings import settings

log = structlog.get_logger(__name__)

_MIN_TEXT_CHARS = 8
_MAX_TEXT_CHARS = 4000

# 1 - cosine_distance = cosine_similarity(pgvector 的 <=> 是距离)。
_LOOKUP_SQL = text(
    """
    SELECT
        response,
        model,
        1 - (request_embedding <=> CAST(:vec AS vector)) AS similarity
    FROM llm_response_cache
    WHERE user_id = :user_id
      AND feature = :feature
      AND model = :model
      AND request_embedding IS NOT NULL
    ORDER BY request_embedding <=> CAST(:vec AS vector)
    LIMIT :candidates
    """
)

_REMEMBER_SQL = text(
    """
    UPDATE llm_response_cache
    SET user_id = :user_id,
        semantic_text = :semantic_text,
        request_embedding = CAST(:vec AS vector)
    WHERE cache_key = :cache_key
    """
)


def enabled(user_id: int | None) -> bool:
    """系统调用(user_id 为空)不参与近似命中 —— 没有归属就没有隔离边界。"""
    return bool(settings.llm_semantic_cache_enabled) and user_id is not None


def _eligible(semantic_text: str) -> bool:
    return _MIN_TEXT_CHARS <= len(semantic_text.strip()) <= _MAX_TEXT_CHARS


async def _embed(semantic_text: str, user_id: int) -> list[float] | None:
    from jobcopilot_api.agents.embedder.agent import embed_batch

    result = await embed_batch([semantic_text.strip()], user_id=user_id)
    return list(result.vectors[0]) if result.vectors else None


async def lookup(
    *,
    user_id: int | None,
    feature: str,
    model: str,
    semantic_text: str,
) -> CachedResponse | None:
    """近似命中。任何异常都降级为 miss —— 缓存层不能把调用链砸了。"""
    if not enabled(user_id) or not _eligible(semantic_text):
        return None
    try:
        vec = await _embed(semantic_text, int(user_id))  # type: ignore[arg-type]
        if vec is None:
            return None
        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(
                    _LOOKUP_SQL,
                    {
                        "vec": _vec_literal(vec),
                        "user_id": user_id,
                        "feature": feature,
                        "model": model,
                        "candidates": settings.llm_semantic_cache_candidates,
                    },
                )
            ).mappings()
            for row in rows:
                if float(row["similarity"]) < settings.llm_semantic_cache_min_similarity:
                    break  # 已按距离排序,第一条不达标后面更不会达标
                payload: dict[str, Any] = row["response"] or {}
                content = payload.get("content")
                if not content:
                    continue
                log.info(
                    "semantic_cache_hit",
                    feature=feature,
                    model=model,
                    similarity=float(row["similarity"]),
                )
                return CachedResponse(
                    content=str(content),
                    tokens_in=int(payload.get("tokens_in") or 0),
                    tokens_out=int(payload.get("tokens_out") or 0),
                    cached_tokens=int(payload.get("cached_tokens") or 0),
                    model=str(row["model"]),
                )
    except Exception as exc:  # noqa: BLE001 - 缓存故障必须降级为 miss
        log.warning("semantic_cache_lookup_failed", error=str(exc))
    return None


async def remember(
    *,
    cache_key: str,
    user_id: int | None,
    semantic_text: str,
) -> None:
    """给刚写入的精确缓存行补上 user_id + 向量,让它以后能被近似命中。"""
    if not enabled(user_id) or not _eligible(semantic_text):
        return
    try:
        vec = await _embed(semantic_text, int(user_id))  # type: ignore[arg-type]
        if vec is None:
            return
        async with get_sessionmaker()() as session:
            await session.execute(
                _REMEMBER_SQL,
                {
                    "cache_key": cache_key,
                    "user_id": user_id,
                    "semantic_text": semantic_text.strip()[:_MAX_TEXT_CHARS],
                    "vec": _vec_literal(vec),
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_cache_remember_failed", error=str(exc))


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
