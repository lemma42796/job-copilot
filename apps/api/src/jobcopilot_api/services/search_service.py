"""Hybrid search(M1,沿用 v1 风格 — 向量 + lexical 双路 + RRF 融合)。

职责(2-TECH §4.1):
- hybrid_search_in_node:节点 prefix 限定的 hybrid search,被 chunk_service /
  quiz_service 用作出题前剪枝(超 30 chunks 时取语义 Top-30)
- global_hybrid_search:跨用户全部笔记的 hybrid search,
  暴露给 AnswerJudge 的 lookup_in_notes_global tool(5-AGENT §4.7,M2)

底层:
- vector 路 — pgvector cosine_distance + HNSW(ix_note_chunks_embedding_hnsw)
- lexical 路 — tsvector @@ to_tsquery + GIN(ix_note_chunks_content_tsv);
  query 走 services/tokenize.to_tsquery_string(char n-gram + ASCII unigram,
  跟 alembic 0014 SQL 端 char_ngrams 函数严格一致)

RRF 融合 score(d) = Σ 1/(RRF_K + rank_i(d)),k=60(Cormack 2009 行业惯例)。
RRF_K / HYBRID_PER_PATH_K 常量在本文件,dogfood 后调动作只改这里。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.agents.embedder.agent import embed_batch
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.services.tokenize import to_tsquery_string

DEFAULT_TOP_K = 30
HYBRID_PER_PATH_K = 60  # 各路召回 K(进 RRF 前),最终截 top_k
RRF_K = 60  # RRF 平滑常数(Cormack 2009)


@dataclass(frozen=True)
class HybridRouteHit:
    """单一路召回结果,供 eval 脚本解释 coarse rank。"""

    chunk: NoteChunk
    rank: int
    score: float


@dataclass(frozen=True)
class HybridSearchDiagnostics:
    """单条 query 内 vector / lexical / RRF 的可解释账本。"""

    query: str
    vector_hits: list[HybridRouteHit]
    lexical_hits: list[HybridRouteHit]
    rrf_scores: dict[int, float]
    fused_all_chunks: list[NoteChunk]
    fused_chunks: list[NoteChunk]


async def hybrid_search_in_node(
    session: AsyncSession,
    folder_path: list[str],
    heading_path: list[str] | None,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[NoteChunk]:
    """节点 prefix 限定 hybrid search(M1)。

    流程:
    1. embed query 走 agents/embedder.embed_batch([query])
    2. 并发跑两路:vector cosine top-K + lexical tsvector top-K
    3. RRF 融合 + 去重 + 截 top_k
    4. lex query 为空(query 短到无 token)→ 优雅降级到纯向量

    必须过滤 WHERE embedding IS NOT NULL(2-TECH §5.5,worker 异步算 embedding,
    未算完的 chunk 不参与召回);folder_path / heading_path prefix 切片对比。
    """
    return await _hybrid_search(
        session,
        query=query,
        top_k=top_k,
        folder_path=folder_path,
        heading_path=heading_path,
    )


async def global_hybrid_search(
    session: AsyncSession,
    query: str,
    top_k: int = 3,
) -> list[NoteChunk]:
    """全用户笔记 hybrid search(AnswerJudge lookup_in_notes_global tool 入口,M2)。

    单用户 MVP 不带 user_id 过滤;M4 多用户时加 user_id WHERE。
    """
    return await _hybrid_search(
        session,
        query=query,
        top_k=top_k,
        folder_path=None,
        heading_path=None,
    )


async def global_hybrid_search_with_diagnostics(
    session: AsyncSession,
    query: str,
    top_k: int = 3,
) -> HybridSearchDiagnostics:
    """全用户笔记 hybrid search + 诊断字段,只给 eval/report 使用。"""
    return await _hybrid_search_with_diagnostics(
        session,
        query=query,
        top_k=top_k,
        folder_path=None,
        heading_path=None,
    )


async def _hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    top_k: int,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
) -> list[NoteChunk]:
    embed_result = await embed_batch([query])
    if not embed_result.vectors:
        return []
    vec = embed_result.vectors[0]
    lex_query = to_tsquery_string(query)
    per_path_k = max(top_k * 2, HYBRID_PER_PATH_K)

    # 顺序跑 vector / lex 两路:SQLAlchemy AsyncSession 不允许同 session 并发
    # SQL(撞 InvalidRequestError "concurrent operations are not permitted")。
    # 两路都是 indexed 查询(HNSW + GIN),串行延迟可控。
    vector_chunks = await _vector_search(
        session, folder_path, heading_path, vec, per_path_k
    )
    if lex_query:
        lexical_chunks = await _lexical_search(
            session, folder_path, heading_path, lex_query, per_path_k
        )
    else:
        lexical_chunks = []

    scores = _rrf_fuse([vector_chunks, lexical_chunks])
    return _by_score(vector_chunks, lexical_chunks, scores)[:top_k]


async def _hybrid_search_with_diagnostics(
    session: AsyncSession,
    *,
    query: str,
    top_k: int,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
) -> HybridSearchDiagnostics:
    embed_result = await embed_batch([query])
    if not embed_result.vectors:
        return HybridSearchDiagnostics(
            query=query,
            vector_hits=[],
            lexical_hits=[],
            rrf_scores={},
            fused_all_chunks=[],
            fused_chunks=[],
        )
    vec = embed_result.vectors[0]
    lex_query = to_tsquery_string(query)
    per_path_k = max(top_k * 2, HYBRID_PER_PATH_K)

    # 顺序跑 vector / lex 两路:SQLAlchemy AsyncSession 不允许同 session 并发
    # SQL(撞 InvalidRequestError "concurrent operations are not permitted")。
    # 两路都是 indexed 查询(HNSW + GIN),串行延迟可控。
    vector_hits = await _vector_search_with_scores(
        session, folder_path, heading_path, vec, per_path_k
    )
    if lex_query:
        lexical_hits = await _lexical_search_with_scores(
            session, folder_path, heading_path, lex_query, per_path_k
        )
    else:
        lexical_hits = []

    vector_chunks = [hit.chunk for hit in vector_hits]
    lexical_chunks = [hit.chunk for hit in lexical_hits]
    scores = _rrf_fuse([vector_chunks, lexical_chunks])
    fused = _by_score(vector_chunks, lexical_chunks, scores)
    return HybridSearchDiagnostics(
        query=query,
        vector_hits=vector_hits,
        lexical_hits=lexical_hits,
        rrf_scores=scores,
        fused_all_chunks=fused,
        fused_chunks=fused[:top_k],
    )


def _apply_prefix_filters(
    stmt: sa.Select[tuple[NoteChunk]],
    folder_path: list[str] | None,
    heading_path: list[str] | None,
) -> sa.Select[tuple[NoteChunk]]:
    if folder_path:
        stmt = stmt.where(
            NoteChunk.folder_path[1 : len(folder_path)] == folder_path
        )
    if heading_path:
        stmt = stmt.where(
            NoteChunk.heading_path[1 : len(heading_path)] == heading_path
        )
    return stmt


async def _vector_search(
    session: AsyncSession,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
    vector: list[float],
    k: int,
) -> list[NoteChunk]:
    """HNSW cosine top-K。WHERE embedding IS NOT NULL 跳过 worker 还没算的 chunk。"""
    stmt = sa.select(NoteChunk).where(NoteChunk.embedding.is_not(None))
    stmt = _apply_prefix_filters(stmt, folder_path, heading_path)
    stmt = stmt.order_by(NoteChunk.embedding.cosine_distance(vector)).limit(k)
    return list((await session.execute(stmt)).scalars().all())


async def _vector_search_with_scores(
    session: AsyncSession,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
    vector: list[float],
    k: int,
) -> list[HybridRouteHit]:
    """HNSW cosine top-K + distance。distance 越小越相似。"""
    distance = NoteChunk.embedding.cosine_distance(vector)
    stmt = sa.select(NoteChunk, distance).where(NoteChunk.embedding.is_not(None))
    stmt = _apply_prefix_filters(stmt, folder_path, heading_path)
    stmt = stmt.order_by(distance).limit(k)
    rows = (await session.execute(stmt)).all()
    return [
        HybridRouteHit(chunk=row[0], rank=rank, score=float(row[1]))
        for rank, row in enumerate(rows, start=1)
    ]


async def _lexical_search(
    session: AsyncSession,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
    ts_query: str,
    k: int,
) -> list[NoteChunk]:
    """tsvector @@ to_tsquery 走 GIN ix_note_chunks_content_tsv;ts_rank 排序。

    content_tsv 是 GENERATED 列,ORM 不暴露(NoteChunk 类 docstring)— 用 raw
    literal_column 引用。
    """
    tsq = sa.func.to_tsquery("simple", ts_query)
    content_tsv: sa.ColumnElement[str] = sa.literal_column("content_tsv")
    rank = sa.func.ts_rank(content_tsv, tsq)
    stmt = sa.select(NoteChunk).where(content_tsv.op("@@")(tsq))
    stmt = _apply_prefix_filters(stmt, folder_path, heading_path)
    stmt = stmt.order_by(rank.desc()).limit(k)
    return list((await session.execute(stmt)).scalars().all())


async def _lexical_search_with_scores(
    session: AsyncSession,
    folder_path: list[str] | None,
    heading_path: list[str] | None,
    ts_query: str,
    k: int,
) -> list[HybridRouteHit]:
    """tsvector lexical top-K + ts_rank。score 越大越匹配。"""
    tsq = sa.func.to_tsquery("simple", ts_query)
    content_tsv: sa.ColumnElement[str] = sa.literal_column("content_tsv")
    rank = sa.func.ts_rank(content_tsv, tsq)
    stmt = sa.select(NoteChunk, rank).where(content_tsv.op("@@")(tsq))
    stmt = _apply_prefix_filters(stmt, folder_path, heading_path)
    stmt = stmt.order_by(rank.desc()).limit(k)
    rows = (await session.execute(stmt)).all()
    return [
        HybridRouteHit(chunk=row[0], rank=idx, score=float(row[1]))
        for idx, row in enumerate(rows, start=1)
    ]


def _rrf_fuse(
    rankings: list[list[NoteChunk]], k: int = RRF_K
) -> dict[int, float]:
    """Reciprocal Rank Fusion(Cormack 2009)。

    score(d) = Σ 1/(k + rank_i(d)),rank 从 1 开始;某 chunk 在某路缺席时
    该项贡献 0(隐含)。返回 {chunk_id: score}。
    """
    scores: dict[int, float] = {}
    for ranked in rankings:
        for rank_idx, ch in enumerate(ranked, start=1):
            scores[ch.id] = scores.get(ch.id, 0.0) + 1.0 / (k + rank_idx)
    return scores


def _by_score(
    vector_chunks: list[NoteChunk],
    lexical_chunks: list[NoteChunk],
    scores: dict[int, float],
) -> list[NoteChunk]:
    """合并去重 + 按 RRF score 降序;同分按 chunk.id 升序保证 deterministic。"""
    by_id: dict[int, NoteChunk] = {}
    for ch in vector_chunks:
        by_id[ch.id] = ch
    for ch in lexical_chunks:
        by_id.setdefault(ch.id, ch)
    return sorted(
        by_id.values(),
        key=lambda c: (-scores.get(c.id, 0.0), c.id),
    )
