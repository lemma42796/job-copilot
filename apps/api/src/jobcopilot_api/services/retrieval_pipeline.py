"""Retrieval pipeline 编排(M2)— 全库 RAG 主战场。

5-AGENT_DESIGN §2.7 流程:

    用户 query
      ↓ query_rewriter (LLM,失败回退原 query)
    expanded_queries(≤ 5,首项必为原 query)
      ↓ asyncio.gather: 各 query 走 global_hybrid_search(vector + lex + RRF)
    K 个 ranked list
      ↓ 跨 query RRF 融合 + 去重
    fused chunks(top ~50 候选)
      ↓ 0 命中守门(< MIN_CHUNKS_FOR_QUIZ → raise NoChunksForQueryError)
      ↓ reranker (qwen3-rerank,失败回退 hybrid 顺序)
    rerank top 10
      ↓ parent-doc 自适应扩展(命中段 < 200 字 → 扩同 H2 兄弟)
      ↓ batch enrich note_title
    PipelineResult{expanded_queries, retrieved_chunks}

quiz_service 拿 PipelineResult 后:expanded_queries / retrieved_chunk_ids
落 quiz_sessions 审计字段;retrieved_chunks 喂 quiz_generator USER 段。
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import NoChunksForQueryError
from jobcopilot_api.models.note import Note
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.schemas.retrieval import PipelineResult, RetrievedChunk
from jobcopilot_api.services.query_rewriter import rewrite_query
from jobcopilot_api.services.reranker import rerank
from jobcopilot_api.services.search_service import global_hybrid_search

logger = logging.getLogger(__name__)

# §2.7 / DATA_MODEL §5.4 — 阈值常量(dogfood 后调动作只改这几行)
HYBRID_TOP_K_PER_QUERY = 50
RERANK_TOP_K = 10
MIN_CHUNKS_FOR_QUIZ = 3  # PRD Q-10:< 3 chunks → 0 命中守门
PARENT_DOC_THRESHOLD_CHARS = 200  # 命中段 < 200 字 → 扩同 H2 兄弟
RRF_K = 60  # 跨 query RRF 平滑常数(同 search_service 内部 RRF 一致)


async def run(
    session: AsyncSession,
    user_query: str,
) -> PipelineResult:
    """全库 RAG retrieval。

    成功 → 返回 PipelineResult(expanded_queries + retrieved_chunks);
    rerank top 10 后命中数 ≥ MIN_CHUNKS_FOR_QUIZ 守门通过(parent-doc 扩展
    不参与守门,仅扩上下文);< 3 抛 NoChunksForQueryError(422)。
    """
    # 1. query_rewriter(失败回退原 query,不阻塞)
    rewrite_out = await rewrite_query(user_query)
    expanded_queries = rewrite_out.expanded_queries

    # 2. 各 expanded query 顺序跑 global_hybrid_search(SQLAlchemy AsyncSession
    # 不允许跨协程并发用同一 session;hybrid_search 内部 vector+lex 两路 gather
    # 保留,这一层串行。expanded_queries ≤ 5,总耗时仍可控)
    hybrid_rankings: list[list[NoteChunk]] = []
    for q in expanded_queries:
        ranking = await global_hybrid_search(
            session, q, top_k=HYBRID_TOP_K_PER_QUERY
        )
        hybrid_rankings.append(ranking)
    fused = multi_query_rrf(hybrid_rankings, k=RRF_K)

    # 3. 0 命中守门(rerank 不增加召回,放在 rerank 前判免一次 LLM 调用)
    if len(fused) < MIN_CHUNKS_FOR_QUIZ:
        raise NoChunksForQueryError(
            f"query='{user_query}' 命中 {len(fused)} 个 chunk,"
            f"少于守门阈值 {MIN_CHUNKS_FOR_QUIZ};请改 query 或先扩笔记"
        )

    # 4. rerank(失败回退 hybrid 顺序前 RERANK_TOP_K)
    rerank_result = await rerank(user_query, fused, top_k=RERANK_TOP_K)

    # 5. parent-doc 自适应扩展
    expanded_scored = await expand_to_parent_docs(session, rerank_result.scored)

    # 6. enrich note_title(batch JOIN 一次)
    note_ids = list({chunk.note_id for chunk, _ in expanded_scored})
    note_titles = await fetch_note_titles(session, note_ids)

    # 7. 组装 RetrievedChunk(保留 rerank 顺序;sibling 扩展插在源 chunk 之后)
    retrieved = [
        RetrievedChunk(
            chunk=chunk,
            folder_path=list(chunk.folder_path),
            heading_path=list(chunk.heading_path),
            note_title=note_titles.get(chunk.note_id, ""),
            rerank_score=score,
        )
        for chunk, score in expanded_scored
    ]

    return PipelineResult(
        expanded_queries=expanded_queries,
        retrieved_chunks=retrieved,
    )


def multi_query_rrf(
    rankings: list[list[NoteChunk]], k: int
) -> list[NoteChunk]:
    """跨 query 的 RRF 融合。

    各 query 内部已 RRF 过 vector + lex(search_service._rrf_fuse),这里
    把 K 个 ranked list 再融一次。score(d) = Σ 1/(k + rank_i(d))。
    返回去重 + score 降序 + 同分按 chunk.id 升序(deterministic)。
    """
    scores: dict[int, float] = {}
    by_id: dict[int, NoteChunk] = {}
    for ranked in rankings:
        for rank_idx, ch in enumerate(ranked, start=1):
            scores[ch.id] = scores.get(ch.id, 0.0) + 1.0 / (k + rank_idx)
            by_id.setdefault(ch.id, ch)
    return sorted(by_id.values(), key=lambda c: (-scores[c.id], c.id))


async def expand_to_parent_docs(
    session: AsyncSession,
    scored: list[tuple[NoteChunk, float]],
) -> list[tuple[NoteChunk, float]]:
    """命中段 < PARENT_DOC_THRESHOLD_CHARS 字的 chunk 扩到同 H2 的 sibling。

    实现:
    - 收集所有"待扩"chunk 的 (note_id, h2_first_seg) 键,记最高 rerank_score
    - 单次 SQL `WHERE note_id IN (set)` 把所有候选 sibling 拉回 Python 过滤
    - sibling chunk 继承源 chunk 的 score(标识"这组一起命中"),保 rerank 顺序

    去重:同 chunk 多次出现取首次(rerank 顺序里靠前的版本)。
    """
    out: dict[int, tuple[NoteChunk, float]] = {}
    pending_keys: set[tuple[int, str]] = set()
    score_by_key: dict[tuple[int, str], float] = {}

    for chunk, score in scored:
        if chunk.id in out:
            continue
        out[chunk.id] = (chunk, score)
        if len(chunk.content) >= PARENT_DOC_THRESHOLD_CHARS:
            continue
        if not chunk.heading_path:
            continue
        key = (chunk.note_id, chunk.heading_path[0])
        pending_keys.add(key)
        # 同 H2 多次命中取最高 score
        if key not in score_by_key or score > score_by_key[key]:
            score_by_key[key] = score

    if not pending_keys:
        return list(out.values())

    note_ids = {k[0] for k in pending_keys}
    stmt = sa.select(NoteChunk).where(NoteChunk.note_id.in_(note_ids))
    candidates = (await session.execute(stmt)).scalars().all()
    for sib in candidates:
        if not sib.heading_path:
            continue
        key = (sib.note_id, sib.heading_path[0])
        if key in pending_keys and sib.id not in out:
            out[sib.id] = (sib, score_by_key[key])

    return list(out.values())


async def fetch_note_titles(
    session: AsyncSession, note_ids: list[int]
) -> dict[int, str]:
    if not note_ids:
        return {}
    stmt = sa.select(Note.id, Note.title).where(Note.id.in_(set(note_ids)))
    rows = (await session.execute(stmt)).all()
    return {row.id: row.title for row in rows}
