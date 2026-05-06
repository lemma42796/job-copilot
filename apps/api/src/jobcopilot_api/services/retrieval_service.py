"""Retrieval service — search profile_chunks for match analysis (S13).

S14 MatchAnalystAgent.retrieve 节点会调用本文件:
  1. `build_match_query(jd)` 把 JDStructured 拼成单条检索 query
  2. `retrieve_for_match(profile_id, query, embedder, k)`
     - embedder.embed([query]) → 1024-dim vector
     - SELECT TOP-K profile_chunks ORDER BY embedding <=> vec

Per 永久约束 #7,embed 调用在任何 DB session 之外执行;只有 SELECT 走 own
session,无副作用,无事务,无写入。

MVP 不做 reranker / hybrid (RRF) / query rewrite — 留 v1.1 (M2 评测扎根
阶段)。当前纯 vector top-K 已经能跑通"召回 → 分析"全链路。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.models import Jd, ProfileChunk

log = structlog.get_logger(__name__)

DEFAULT_TOP_K = 10
RESPONSIBILITY_PREVIEW_LIMIT = 5


@dataclass(frozen=True)
class RetrieveResult:
    """Per-call summary handed to the analyze 节点 / SSE router。"""

    chunks: list[ProfileChunk]
    query: str
    embed_model: str
    tokens_in: int
    cost_cny: Decimal


def build_match_query(jd: Jd) -> str:
    """Build a single search-query string from a parsed JD ORM row.

    MVP 直接拼 title + hard_skills 名 + responsibilities top-N。Multi-query
    (QueryRewriterAgent in 5-AGENT_DESIGN §5) 推到 v1.1。

    `hard_skills` / `responsibilities` 在 ORM 上是 JSONB list[dict] / list[str];
    缺位/类型怪异时跳过该段而不抛错(MVP 容错,后续 evals 再卡严格性)。
    """
    parts: list[str] = []
    if jd.title:
        parts.append(f"岗位:{jd.title}")
    if jd.hard_skills:
        names = [
            str(s["name"])
            for s in jd.hard_skills
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"].strip()
        ]
        if names:
            parts.append("硬技能:" + ", ".join(names))
    if jd.responsibilities:
        first = [str(r) for r in list(jd.responsibilities)[:RESPONSIBILITY_PREVIEW_LIMIT] if r]
        if first:
            parts.append("职责:" + " | ".join(first))
    return "\n".join(parts)


async def retrieve_for_match(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: int,
    query_text: str,
    embedder: Embedder,
    k: int = DEFAULT_TOP_K,
) -> RetrieveResult:
    """Embed `query_text` and return the top-K chunks for `profile_id`.

    Embed 在 DB session 外执行(永久约束 #7);只有 SELECT 走 own session。
    """
    embed = await embedder.embed([query_text])
    if not embed.vectors:
        return RetrieveResult(
            chunks=[],
            query=query_text,
            embed_model=embedder.model,
            tokens_in=0,
            cost_cny=Decimal("0"),
        )
    vec = embed.vectors[0]

    async with sessionmaker() as session:
        rows = await _search_chunks(session, profile_id=profile_id, vector=vec, k=k)

    log.info(
        "retrieval_service.retrieve",
        profile_id=profile_id,
        k=k,
        chunks_returned=len(rows),
        embed_model=embedder.model,
        tokens_in=embed.tokens_in,
        cost_cny=str(embed.cost_cny),
    )
    return RetrieveResult(
        chunks=rows,
        query=query_text,
        embed_model=embedder.model,
        tokens_in=embed.tokens_in,
        cost_cny=embed.cost_cny,
    )


async def _search_chunks(
    session: AsyncSession,
    *,
    profile_id: int,
    vector: list[float],
    k: int,
) -> list[ProfileChunk]:
    """Pure read; profile-scoped + filters out NULL embeddings (chunker
    rebuild 失败的残留行)。HNSW index `idx_pc_embedding_hnsw` 自动命中。"""
    # `cosine_distance` is registered by pgvector on the Vector(1024) column
    # via SQLAlchemy's comparator hook — visible at both runtime and to mypy
    # since pgvector ships type stubs.
    stmt = (
        sa.select(ProfileChunk)
        .where(
            ProfileChunk.profile_id == profile_id,
            ProfileChunk.embedding.is_not(None),
        )
        .order_by(ProfileChunk.embedding.cosine_distance(vector))
        .limit(k)
    )
    return list((await session.execute(stmt)).scalars().all())


async def load_all_profile_chunks(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: int,
) -> list[ProfileChunk]:
    """Load **all** chunks for a profile, ordered by id.

    Reviewer 用这个拿完整事实库做核查 — `retrieve_for_match` 走 JD-anchored
    Top-K 会漏掉 JD-不相关的 education / language skill chunks,触发 reviewer
    假阳性 [M4](见 STATUS.md S21 子任务 3 后的修复)。Reviewer 是单文档全文
    事实核查,不应走相关性召回。
    """
    async with sessionmaker() as session:
        stmt = (
            sa.select(ProfileChunk)
            .where(
                ProfileChunk.profile_id == profile_id,
                ProfileChunk.embedding.is_not(None),
            )
            .order_by(ProfileChunk.id)
        )
        return list((await session.execute(stmt)).scalars().all())
