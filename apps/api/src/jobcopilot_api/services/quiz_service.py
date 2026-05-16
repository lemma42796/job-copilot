"""出题编排 service(M2,4-API_SPEC §4.1 / §4.6)。

职责:
- POST /api/quiz/sessions(SSE)入口
- 内联 retrieval 5 步(query_rewriter → multi-query hybrid → rerank/blend →
  selected seed chunks → enrich note_title)+ quiz_generator LLM 出题
- service 后处理:[N] → DB id 映射 + 完整性校验
  (reference_chunk_ids ⊆ source_chunk_ids / weight 之和 ∈ [0.99, 1.01] /
  reference_points evidence_chunk_ids ⊆ source_chunk_ids)
- 落库:questions × N + UPDATE quiz_sessions audit + session_answers × N
- 事件流:started / progress × M / question_ready × N / done

事务策略:每个写阶段独立 session(SSE 长流不能跨阶段持有同一 session)。

错误降级:
- NoChunksForQueryError → emit error{no_chunks_for_query} + done(false)
  + UPDATE quiz_sessions.status=abandoned
- 完整性校验失败 / LLM 失败 → emit error{llm_call_failed} + done(false) + abandon
- 其他未预期 → emit error{internal_error} + done(false) + abandon
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.agents.quiz_generator import agent as quiz_generator_agent
from jobcopilot_api.agents.quiz_generator.prompts import (
    PROMPT_VERSION as QUIZ_PROMPT_VERSION,
)
from jobcopilot_api.errors import JobCopilotError, NoChunksForQueryError
from jobcopilot_api.llm.client import LLMResult
from jobcopilot_api.llm.errors import LLMError
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.schemas.agents.quiz_generator import (
    QuizGenChunkInput,
    QuizGenInput,
    QuizGenOutput,
)
from jobcopilot_api.schemas.quiz import QuizSessionCreateIn
from jobcopilot_api.schemas.retrieval import RetrievedChunk
from jobcopilot_api.services.query_rewriter import query_weights, rewrite_query
from jobcopilot_api.services.reranker import rerank
from jobcopilot_api.services.retrieval_governance import (
    PROTECTED_ANCHOR_ROUTE_WEIGHT,
    assess_query_support,
    governance_context_from_rewrite,
    post_rerank_governance_blend,
    protected_anchor_search,
)
from jobcopilot_api.services.retrieval_pipeline import (
    HYBRID_TOP_K_PER_QUERY,
    MIN_CHUNKS_FOR_QUIZ,
    POST_RERANK_PROVIDER_TOP_K,
    RERANK_TOP_K,
    RRF_K,
    fetch_note_titles,
    multi_query_rrf,
)
from jobcopilot_api.services.search_service import global_hybrid_search

logger = logging.getLogger(__name__)

REFERENCE_POINTS_MIN = 2
REFERENCE_POINTS_MAX = 5
EVIDENCE_SUPPORT_MIN_SCORE = 0.18
EVIDENCE_SUPPORT_MIN_HITS = 2
_CITATION_RE = re.compile(r"\[(\d+)\]")
_LATIN_TERM_RE = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}|\d+(?:\.\d+)?[a-zA-Z]*"
)
_EVIDENCE_STOPWORDS = {
    "and",
    "are",
    "for",
    "how",
    "the",
    "this",
    "that",
    "what",
    "when",
    "why",
    "with",
}


class IntegrityCheckError(JobCopilotError):
    """quiz_generator 输出过 LLMClient pydantic 校验,但完整性约束失败
    (reference_chunk_ids / weight / type_mix 等);上报 SSE 时映射到
    `llm_call_failed`(4-API_SPEC §4.1 错误码列表)。"""

    status_code = 502
    code = "llm_call_failed"
    title = "LLM 输出不符合完整性约束"


# ---------- 主入口 ----------


async def start_session_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    payload: QuizSessionCreateIn,
) -> AsyncIterator[dict[str, Any]]:
    """SSE 事件流(4-API_SPEC §4.1 / §4.6)。

    yield dict 形态 `{"event": "<name>", "data": "<json string>"}`
    给 EventSourceResponse 直接转发。
    """
    session_id: int | None = None
    try:
        session_id = await _create_quiz_session(sessionmaker, payload)
        yield _ev(
            "started",
            {
                "resource_id": session_id,
                "query": payload.query,
                "mode": payload.mode,
            },
        )

        # 1. query rewrite
        yield _ev("progress", {"phase": "query_rewriting"})
        rewrite_out = await rewrite_query(payload.query)
        expanded_queries = rewrite_out.expanded_queries
        weights = query_weights(rewrite_out)
        governance = governance_context_from_rewrite(rewrite_out)
        yield _ev(
            "progress",
            {
                "phase": "query_rewriting_done",
                "expanded_queries": expanded_queries,
            },
        )

        # 2-5. retrieval 4 步(共享一个只读 session)
        async with sessionmaker() as s:
            # 2. multi-query hybrid
            hybrid_rankings: list[list[NoteChunk]] = []
            for q in expanded_queries:
                ranking = await global_hybrid_search(
                    s, q, top_k=HYBRID_TOP_K_PER_QUERY
                )
                hybrid_rankings.append(ranking)
            anchor_ranking = await protected_anchor_search(
                s, governance, expanded_queries
            )
            if anchor_ranking:
                hybrid_rankings.append(anchor_ranking)
                weights.append(PROTECTED_ANCHOR_ROUTE_WEIGHT)
            fused = multi_query_rrf(
                hybrid_rankings,
                k=RRF_K,
                weights=weights,
                governance=governance,
            )
            yield _ev(
                "progress",
                {"phase": "hybrid_searching", "candidate_count": len(fused)},
            )

            # 3. 0 命中守门
            if len(fused) < MIN_CHUNKS_FOR_QUIZ:
                raise NoChunksForQueryError(
                    f"笔记里没找到跟「{payload.query}」相关的内容,"
                    f"试试别的主题或先写一些笔记"
                )
            support = assess_query_support(governance, expanded_queries, fused)
            if not support.sufficient:
                raise NoChunksForQueryError(
                    f"笔记里没找到能支撑「{payload.query}」的核心证据,"
                    f"缺少:{', '.join(support.missing_terms)}"
                )

            # 4. rerank + dynamic clean-context selection
            yield _ev("progress", {"phase": "reranking"})
            rerank_result = await rerank(
                payload.query,
                fused,
                top_k=min(POST_RERANK_PROVIDER_TOP_K, len(fused)),
            )
            post_rerank = post_rerank_governance_blend(
                fused,
                rerank_result.scored,
                governance,
                expanded_queries,
                top_k=RERANK_TOP_K,
            )

            # 5. Keep only selected seed chunks. Parent-doc expansion is
            # disabled until evidence/background chunks are separated.
            selected_scored = post_rerank.selected
            note_ids = list({chunk.note_id for chunk, _ in selected_scored})
            note_titles = await fetch_note_titles(s, note_ids)

        retrieved_chunks = [
            RetrievedChunk(
                chunk=chunk,
                folder_path=list(chunk.folder_path),
                heading_path=list(chunk.heading_path),
                note_title=note_titles.get(chunk.note_id, ""),
                rerank_score=score,
            )
            for chunk, score in selected_scored
        ]
        yield _ev(
            "progress",
            {
                "phase": "context_selecting",
                "chunk_count": len(retrieved_chunks),
            },
        )

        # 6. UPDATE quiz_sessions audit 字段
        retrieved_chunk_ids = [rc.chunk.id for rc in retrieved_chunks]
        await _update_session_audit(
            sessionmaker,
            session_id,
            expanded_queries=expanded_queries,
            retrieved_chunk_ids=retrieved_chunk_ids,
        )

        # 7. quiz_generator LLM
        yield _ev(
            "progress", {"phase": "generating", "model": "qwen3.6-flash"}
        )
        gen_chunks = [
            QuizGenChunkInput(
                id=rc.chunk.id,
                folder_path=rc.folder_path,
                heading_path=rc.heading_path,
                note_title=rc.note_title,
                content=rc.chunk.content,
            )
            for rc in retrieved_chunks
        ]
        gen_input = QuizGenInput(
            query=payload.query,
            mode=payload.mode,
            chunks=gen_chunks,
            question_count=payload.question_count,
        )
        try:
            llm_result = await quiz_generator_agent.run(gen_input)
        except LLMError as e:
            raise IntegrityCheckError(f"quiz_generator LLM 调用失败:{e}") from e

        gen_output = llm_result.parsed
        if not isinstance(gen_output, QuizGenOutput):
            raise IntegrityCheckError(
                "quiz_generator 没返回有效的 QuizGenOutput"
            )

        # 8. 后处理 + 完整性校验
        rows = _build_question_rows(
            gen_output=gen_output,
            gen_chunks=gen_chunks,
            query=payload.query,
            mode=payload.mode,
            llm_result=llm_result,
        )
        yield _ev(
            "progress",
            {
                "phase": "type_mix_decided",
                "type_mix": gen_output.type_mix.model_dump(),
            },
        )

        # 9. INSERT questions + session_answers
        question_rows = await _insert_questions_and_answers(
            sessionmaker, session_id, rows
        )

        # 10. emit question_ready × N
        for idx, q in enumerate(question_rows):
            yield _ev(
                "question_ready",
                {
                    "order_index": idx,
                    "question": {
                        "id": q.id,
                        "type": q.type,
                        "prompt": q.prompt,
                        "source_chunk_ids": q.source_chunk_ids,
                    },
                },
            )

        yield _ev("done", {"ok": True})

    except NoChunksForQueryError as e:
        if session_id is not None:
            await _mark_session_abandoned(sessionmaker, session_id)
        yield _ev(
            "error", {"code": "no_chunks_for_query", "detail": e.detail}
        )
        yield _ev("done", {"ok": False})
    except IntegrityCheckError as e:
        if session_id is not None:
            await _mark_session_abandoned(sessionmaker, session_id)
        yield _ev("error", {"code": e.code, "detail": e.detail})
        yield _ev("done", {"ok": False})
    except Exception as e:
        logger.exception("quiz session %s 内部错误", session_id)
        if session_id is not None:
            await _mark_session_abandoned(sessionmaker, session_id)
        yield _ev(
            "error", {"code": "internal_error", "detail": str(e)}
        )
        yield _ev("done", {"ok": False})


# ---------- 内部 helpers ----------


def _ev(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """sse-starlette EventSourceResponse 接受 `{event, data}` dict。

    手动 json.dumps + ensure_ascii=False 让中文 detail / heading 不被
    escape 成 \\uXXXX(默认 sse-starlette 序列化会 escape)。
    """
    return {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }


async def _create_quiz_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    payload: QuizSessionCreateIn,
) -> int:
    """预占 quiz_sessions 行,返回 session_id;status / started_at /
    trigger / mode 走 server_default(mode 也有 default 但显式传更清晰)。"""
    async with sessionmaker() as s:
        qs = QuizSession(
            query=payload.query,
            mode=payload.mode,
            jd_ids=payload.jd_ids,
            trigger="manual",
        )
        s.add(qs)
        await s.commit()
        return qs.id


async def _update_session_audit(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    *,
    expanded_queries: list[str],
    retrieved_chunk_ids: list[int],
) -> None:
    async with sessionmaker() as s:
        await s.execute(
            sa.update(QuizSession)
            .where(QuizSession.id == session_id)
            .values(
                expanded_queries=expanded_queries,
                retrieved_chunk_ids=retrieved_chunk_ids,
            )
        )
        await s.commit()


async def _mark_session_abandoned(
    sessionmaker: async_sessionmaker[AsyncSession], session_id: int
) -> None:
    async with sessionmaker() as s:
        await s.execute(
            sa.update(QuizSession)
            .where(QuizSession.id == session_id)
            .values(
                status="abandoned",
                abandoned_at=datetime.now(UTC),
            )
        )
        await s.commit()


def _build_question_rows(
    *,
    gen_output: QuizGenOutput,
    gen_chunks: list[QuizGenChunkInput],
    query: str,
    mode: str,
    llm_result: LLMResult,
) -> list[dict[str, Any]]:
    """[N] → DB id 映射 + 完整性校验。

    LLM 输出的 source_chunk_ids / reference_chunk_ids / evidence_chunk_ids
    都是 1-based [N] 编号(对应 USER 段渲染顺序),映射到 chunks[N-1].id。
    任何越界 / 集合包含违反 / evidence 引用不可信 / weight 之和不在
    [0.99, 1.01] → IntegrityCheckError。
    """
    type_mix = gen_output.type_mix
    if type_mix.open_ended + type_mix.definition != len(gen_output.questions):
        raise IntegrityCheckError(
            f"type_mix({type_mix.open_ended}+{type_mix.definition}) 跟 "
            f"questions 数 {len(gen_output.questions)} 不匹配"
        )

    chunk_db_ids = [c.id for c in gen_chunks]
    n_chunks = len(gen_chunks)

    rows: list[dict[str, Any]] = []
    for q in gen_output.questions:
        if not q.source_chunk_ids:
            raise IntegrityCheckError(
                f"题 '{q.prompt[:30]}...' source_chunk_ids 为空"
            )
        for n in q.source_chunk_ids:
            if not (1 <= n <= n_chunks):
                raise IntegrityCheckError(
                    f"source_chunk_ids 含越界编号 [{n}](合法 1..{n_chunks})"
                )
        source_db = [chunk_db_ids[n - 1] for n in q.source_chunk_ids]

        source_set = set(q.source_chunk_ids)
        for n in q.reference_chunk_ids:
            if n not in source_set:
                raise IntegrityCheckError(
                    f"reference_chunk_ids [{n}] 不在 source_chunk_ids {sorted(source_set)} 里"
                )
        _validate_reference_answer_citations(q)
        ref_db = [chunk_db_ids[n - 1] for n in q.reference_chunk_ids]

        _validate_reference_points_shape(q)
        weight_sum = sum(p.weight for p in q.reference_points)
        if not (0.99 <= weight_sum <= 1.01):
            raise IntegrityCheckError(
                f"题 '{q.prompt[:30]}...' reference_points weight 之和 "
                f"{weight_sum:.3f} 超出 [0.99, 1.01]"
            )

        ref_points = []
        for p in q.reference_points:
            for n in p.evidence_chunk_ids:
                if n not in source_set:
                    raise IntegrityCheckError(
                        f"reference_point '{p.id}' evidence_chunk_ids [{n}] "
                        f"不在 source_chunk_ids 里"
                    )
            ref_points.append(
                {
                    "id": p.id,
                    "text": p.text,
                    "weight": p.weight,
                    "evidence_chunk_ids": [
                        chunk_db_ids[n - 1] for n in p.evidence_chunk_ids
                    ],
                }
            )

        _validate_question_evidence_support(q, gen_chunks)

        rows.append(
            {
                "originated_query": query,
                "originated_mode": mode,
                "type": q.type,
                "prompt": q.prompt,
                "source_chunk_ids": source_db,
                "reference_answer": q.reference_answer,
                "reference_chunk_ids": ref_db,
                "reference_points": ref_points,
                "gen_model": llm_result.model,
                "gen_prompt_version": QUIZ_PROMPT_VERSION,
                "gen_tokens_in": llm_result.tokens_in,
                "gen_tokens_out": llm_result.tokens_out,
                "gen_cost_cny": llm_result.cost_cny,
            }
        )
    return rows


def _validate_reference_answer_citations(
    q: GeneratedQuestion,
) -> None:
    """Ensure reference_answer cites declared local chunk ids."""
    if not q.reference_chunk_ids:
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_chunk_ids 为空"
        )
    cited = {int(raw) for raw in _CITATION_RE.findall(q.reference_answer)}
    if not cited:
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_answer 缺少 [N] 引用"
        )
    reference_set = set(q.reference_chunk_ids)
    invalid = sorted(cited - reference_set)
    if invalid:
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_answer 引用了非 reference chunks:{invalid}"
        )
    missing = sorted(reference_set - cited)
    if missing:
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_answer 未引用 reference_chunk_ids:{missing}"
        )


def _validate_reference_points_shape(q: GeneratedQuestion) -> None:
    point_count = len(q.reference_points)
    if not (REFERENCE_POINTS_MIN <= point_count <= REFERENCE_POINTS_MAX):
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_points 数量 {point_count} "
            f"不在 [{REFERENCE_POINTS_MIN}, {REFERENCE_POINTS_MAX}]"
        )
    point_ids = [p.id for p in q.reference_points]
    if len(point_ids) != len(set(point_ids)):
        raise IntegrityCheckError(
            f"题 '{q.prompt[:30]}...' reference_points id 重复"
        )
    for p in q.reference_points:
        if p.weight <= 0:
            raise IntegrityCheckError(
                f"题 '{q.prompt[:30]}...' reference_point '{p.id}' weight 非正"
            )
        if not p.evidence_chunk_ids:
            raise IntegrityCheckError(
                f"题 '{q.prompt[:30]}...' reference_point '{p.id}' "
                "evidence_chunk_ids 为空"
            )


def _validate_question_evidence_support(
    q: GeneratedQuestion,
    chunks: list[QuizGenChunkInput],
) -> None:
    chunk_by_local = {idx: chunk for idx, chunk in enumerate(chunks, start=1)}
    for p in q.reference_points:
        evidence_text = "\n".join(
            chunk_by_local[n].content for n in p.evidence_chunk_ids
        )
        support = _evidence_support(p.text, evidence_text)
        if not support:
            raise IntegrityCheckError(
                f"题 '{q.prompt[:30]}...' reference_point '{p.id}' "
                "和 evidence_chunk_ids 缺少足够文本重合"
            )


def _evidence_support(claim_text: str, evidence_text: str) -> bool:
    claim_units = _evidence_units(claim_text)
    if not claim_units:
        return bool(claim_text.strip() and claim_text.strip() in evidence_text)
    evidence_units = _evidence_units(evidence_text)
    hits = claim_units & evidence_units
    if len(claim_units) <= 3:
        return bool(hits)
    return (
        len(hits) >= min(EVIDENCE_SUPPORT_MIN_HITS, len(claim_units))
        and len(hits) / len(claim_units) >= EVIDENCE_SUPPORT_MIN_SCORE
    )


def _evidence_units(text: str) -> set[str]:
    normalized = text.lower()
    units = {
        item
        for item in _LATIN_TERM_RE.findall(normalized)
        if item not in _EVIDENCE_STOPWORDS
    }
    cjk_chars = [ch for ch in normalized if "\u4e00" <= ch <= "\u9fff"]
    units.update(
        "".join(cjk_chars[idx : idx + 2])
        for idx in range(max(len(cjk_chars) - 1, 0))
    )
    return units


async def _insert_questions_and_answers(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: int,
    rows: list[dict[str, Any]],
) -> list[Question]:
    """批量 INSERT questions 拿 ids → INSERT session_answers × N。

    expire_on_commit=False 在 sessionmaker 配置里,commit 后 question 实例
    的字段(id / type / prompt / source_chunk_ids)仍可读不会 lazy load。
    """
    async with sessionmaker() as s:
        questions: list[Question] = []
        for r in rows:
            q = Question(**r)
            s.add(q)
            questions.append(q)
        await s.flush()  # 拿 questions.id

        for idx, q in enumerate(questions):
            s.add(
                SessionAnswer(
                    session_id=session_id,
                    question_id=q.id,
                    order_index=idx,
                )
            )
        await s.commit()
        return questions
