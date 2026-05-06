"""Retrieval service — search profile_chunks for match analysis (S13).

S14 MatchAnalystAgent.retrieve / S19 ResumeGraph.retrieve 节点都调用本文件:
  1. `build_match_query(jd)` 把 JDStructured 拼成单条检索 query
  2. `retrieve_for_match` (S13 baseline,纯向量 top-K)— 保留作为 evals v0
     baseline,生产路径已切到 hybrid。
  3. `hybrid_retrieve_for_match` (S21 子任务 4-A)— 向量 + lexical(字符 n-gram
     tsvector)双路并发 + RRF 融合,生产默认。
  4. `load_all_profile_chunks` (S21 子任务 3)— Reviewer 用全量 chunks 做事实
     核查,不走相关性召回(永久约束 [来自 S21 第二轮 dogfood])。

Per 永久约束 #7,embed 调用在任何 DB session 之外执行;只有 SELECT 走 own
session,无副作用,无事务,无写入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.models import Jd, ProfileChunk
from jobcopilot_api.services.tokenize import to_tsquery_string

log = structlog.get_logger(__name__)

DEFAULT_TOP_K = 10
HYBRID_PER_PATH_K = 20  # 各路召回 K(送入 RRF 前),最终截 DEFAULT_TOP_K
RRF_K = 60  # RRF 平滑常数(行业惯例,Cormack 2009)
RESPONSIBILITY_PREVIEW_LIMIT = 5


@dataclass(frozen=True)
class RetrieveResult:
    """Per-call summary handed to the analyze 节点 / SSE router。

    `chunks` 是最终给 LLM 的 top-K(纯向量 / hybrid 都填这个)。其余字段是
    ablation / 评测用,生产 caller 只读 chunks(永久约束:caller 接口稳定)。
    """

    chunks: list[ProfileChunk]
    query: str
    embed_model: str
    tokens_in: int
    cost_cny: Decimal
    # S21 4-A — hybrid ablation 字段(纯向量调用时全部为 None)
    vector_chunks: list[ProfileChunk] | None = field(default=None)
    lexical_chunks: list[ProfileChunk] | None = field(default=None)
    lexical_query: str | None = field(default=None)
    rrf_scores: dict[int, float] | None = field(default=None)


def build_match_query(jd: Jd) -> str:
    """Build a single search-query string from a parsed JD ORM row.

    MVP 直接拼 title + hard_skills 名 + responsibilities top-N。Multi-query
    (QueryRewriterAgent in 5-AGENT_DESIGN §5) 推到后面。

    `hard_skills` / `responsibilities` 在 ORM 上是 JSONB list[dict] / list[str];
    缺位 / 类型怪异时跳过该段而不抛错。
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
    """V0 baseline:纯向量 top-K。

    保留供 evals 跑 v0 vs v1(hybrid)ablation 对照。生产路径用
    `hybrid_retrieve_for_match`。
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
        rows = await _vector_search(session, profile_id=profile_id, vector=vec, k=k)

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


async def hybrid_retrieve_for_match(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: int,
    query_text: str,
    embedder: Embedder,
    k: int = DEFAULT_TOP_K,
    per_path_k: int | None = None,
) -> RetrieveResult:
    """Hybrid 检索:向量 + lexical(字符 n-gram tsvector)双路并发 + RRF 融合。

    1. 并发跑两路:
       (a) 向量 cosine top-`per_path_k`(HNSW 索引 `idx_pc_embedding_hnsw`)
       (b) lexical tsvector @@ to_tsquery,按 ts_rank 排序 top-`per_path_k`
           (GIN 索引 `idx_pc_content_tsv`,ngram 切分见 alembic 0014)
    2. 应用层 RRF 融合:`score(d) = Σ 1/(RRF_K + rank_i(d))`(任一路出现就累加),
       合并去重后按 score 降序截 top-`k`。
    3. lexical 路 query 为空(纯标点 / 短到无 token)时优雅降级到纯向量。

    返回 `RetrieveResult.chunks` 是最终 top-`k`,`vector_chunks` /
    `lexical_chunks` / `rrf_scores` 是 ablation 字段(评测用)。
    """
    # 默认 per_path_k = max(2*k, HYBRID_PER_PATH_K),保证 fusion 有足够候选拉开
    # RRF 排名差异;否则两路都只取 final k,RRF 退化为简单去重。
    effective_per_path_k = (
        per_path_k if per_path_k is not None else max(2 * k, HYBRID_PER_PATH_K)
    )

    embed = await embedder.embed([query_text])
    if not embed.vectors:
        return RetrieveResult(
            chunks=[],
            query=query_text,
            embed_model=embedder.model,
            tokens_in=0,
            cost_cny=Decimal("0"),
            vector_chunks=[],
            lexical_chunks=[],
            lexical_query="",
            rrf_scores={},
        )
    vec = embed.vectors[0]
    lex_query = to_tsquery_string(query_text)

    async def _run_vector() -> list[ProfileChunk]:
        async with sessionmaker() as session:
            return await _vector_search(
                session, profile_id=profile_id, vector=vec, k=effective_per_path_k
            )

    async def _run_lexical() -> list[ProfileChunk]:
        if not lex_query:
            return []
        async with sessionmaker() as session:
            return await _lexical_search(
                session,
                profile_id=profile_id,
                ts_query=lex_query,
                k=effective_per_path_k,
            )

    vector_chunks, lexical_chunks = await asyncio.gather(_run_vector(), _run_lexical())

    rrf_scores = _rrf_fuse([vector_chunks, lexical_chunks], k=RRF_K)
    fused = _by_score(vector_chunks, lexical_chunks, rrf_scores)[:k]

    log.info(
        "retrieval_service.hybrid_retrieve",
        profile_id=profile_id,
        k=k,
        per_path_k=effective_per_path_k,
        vector_returned=len(vector_chunks),
        lexical_returned=len(lexical_chunks),
        fused_returned=len(fused),
        embed_model=embedder.model,
        tokens_in=embed.tokens_in,
        cost_cny=str(embed.cost_cny),
        lexical_query_empty=not lex_query,
    )
    return RetrieveResult(
        chunks=fused,
        query=query_text,
        embed_model=embedder.model,
        tokens_in=embed.tokens_in,
        cost_cny=embed.cost_cny,
        vector_chunks=vector_chunks,
        lexical_chunks=lexical_chunks,
        lexical_query=lex_query,
        rrf_scores=rrf_scores,
    )


async def _vector_search(
    session: AsyncSession,
    *,
    profile_id: int,
    vector: list[float],
    k: int,
) -> list[ProfileChunk]:
    """Pure read; profile-scoped + filters out NULL embeddings (chunker
    rebuild 失败的残留行)。HNSW index `idx_pc_embedding_hnsw` 自动命中。"""
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


async def _lexical_search(
    session: AsyncSession,
    *,
    profile_id: int,
    ts_query: str,
    k: int,
) -> list[ProfileChunk]:
    """tsvector 关键字检索(字符 n-gram 切分,见 alembic 0014)。

    `content_tsv @@ to_tsquery(...)` 走 GIN `idx_pc_content_tsv`;`ts_rank` 仅
    排序无 index 加速,在 ngram OR query 下随命中数 / 密度自然给 BM25-lite 排名。

    `content_tsv` 列故意没 ORM-mapped(见 ProfileChunk 类注释),用 raw
    `literal_column` 引用。
    """
    tsq = sa.func.to_tsquery("simple", ts_query)
    content_tsv = sa.literal_column("content_tsv")
    rank = sa.func.ts_rank(content_tsv, tsq)
    stmt = (
        sa.select(ProfileChunk)
        .where(
            ProfileChunk.profile_id == profile_id,
            content_tsv.op("@@")(tsq),
        )
        .order_by(rank.desc())
        .limit(k)
    )
    return list((await session.execute(stmt)).scalars().all())


def _rrf_fuse(
    rankings: list[list[ProfileChunk]],
    *,
    k: int = RRF_K,
) -> dict[int, float]:
    """Reciprocal Rank Fusion (Cormack 2009)。

    `score(d) = Σ_{i} 1/(k + rank_i(d))`,rank 从 1 开始;某 chunk 在某路缺席
    时该项贡献为 0(隐含)。返回 `{chunk_id: score}`。

    自己实现非调 LangChain `EnsembleRetriever`,因为评测时要看两路 score
    分布做 ablation。
    """
    scores: dict[int, float] = {}
    for ranked in rankings:
        for rank_idx, ch in enumerate(ranked, start=1):
            scores[ch.id] = scores.get(ch.id, 0.0) + 1.0 / (k + rank_idx)
    return scores


def _by_score(
    vector_chunks: list[ProfileChunk],
    lexical_chunks: list[ProfileChunk],
    scores: dict[int, float],
) -> list[ProfileChunk]:
    """合并去重 + 按 RRF score 降序。同分按 chunk.id 升序保证 deterministic。"""
    by_id: dict[int, ProfileChunk] = {}
    for ch in vector_chunks:
        by_id[ch.id] = ch
    for ch in lexical_chunks:
        by_id.setdefault(ch.id, ch)
    return sorted(
        by_id.values(),
        key=lambda c: (-scores.get(c.id, 0.0), c.id),
    )


async def load_all_profile_chunks(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: int,
) -> list[ProfileChunk]:
    """Load **all** chunks for a profile, ordered by id.

    Reviewer 用这个拿完整事实库做核查 — `retrieve_for_match` /
    `hybrid_retrieve_for_match` 都走 JD-anchored 召回会漏掉 JD-不相关的
    education / language skill chunks,触发 reviewer 假阳性 [M4](见
    STATUS.md S21 子任务 3 后的修复)。Reviewer 是单文档全文事实核查,
    不应走相关性召回 — hybrid 也只是改进相关性,不是 100% 召回。
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
