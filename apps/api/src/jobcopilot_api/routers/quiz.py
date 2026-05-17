"""Quiz REST + SSE 端点(M2,4-API_SPEC §4.1 / §4.6)。

router 自身 prefix `/quiz`;main.py include 时挂 `/api` 前缀,实际端点路径
= `/api/quiz/*`。当前实现 M2 出题、答题草稿、提交评分、放弃会话端点。
GET /api/quiz/sessions/{id} 用于刷新恢复 / 历史回看。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from jobcopilot_api.errors import (
    ModeNotImplementedError,
    QueryRequiredError,
    QueryTooLongError,
)
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.schemas.quiz import (
    AnswerDraftIn,
    AnswerTurnSubmitIn,
    QuizSessionCreateIn,
    QuizSessionDetailOut,
    QuizSessionListOut,
)
from jobcopilot_api.services import answer_service, interview_service, quiz_service

router = APIRouter(tags=["quiz"], prefix="/quiz")

QUERY_MAX_LENGTH = 200


@router.post(
    "/sessions",
    summary="聊天框 query 出题(SSE)",
)
async def create_session(payload: QuizSessionCreateIn) -> EventSourceResponse:
    """4-API_SPEC §4.1。

    入参 `{query, mode, question_count, jd_ids?}`,SSE 流见 §4.6:
    started → progress(query_rewriting / hybrid / rerank / context_selecting /
    generating / type_mix_decided)→ question_ready × N → done。
    """
    # 4-API_SPEC §4.1 错误码(M2 阶段):
    if payload.mode in ("job", "auto"):
        raise ModeNotImplementedError(
            f"M2 阶段仅支持 mode=topic;mode={payload.mode!r} 在 M3 启用"
        )
    if payload.mode == "topic" and not payload.query.strip():
        raise QueryRequiredError("mode=topic 时 query 不能为空")
    if len(payload.query) > QUERY_MAX_LENGTH:
        raise QueryTooLongError(
            f"query 长度 {len(payload.query)} > {QUERY_MAX_LENGTH}"
        )

    sessionmaker = get_sessionmaker()
    return EventSourceResponse(
        quiz_service.start_session_sse(sessionmaker, payload)
    )


@router.get(
    "/sessions",
    summary="查询最近答题会话",
)
async def list_sessions(
    status: Literal["in_progress", "submitted", "abandoned"] | None = None,
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> QuizSessionListOut:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        return await answer_service.list_sessions(
            session,
            status=status,
            cursor=cursor,
            limit=limit,
        )


@router.get(
    "/sessions/{session_id}",
    summary="查询答题会话详情",
)
async def get_session(session_id: int) -> QuizSessionDetailOut:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        return await answer_service.get_session_detail(session, session_id)


@router.put(
    "/sessions/{session_id}/answers/{order_index}",
    summary="保存单题答案草稿",
)
async def save_answer(
    session_id: int,
    order_index: int,
    payload: AnswerDraftIn,
) -> dict[str, bool]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await answer_service.save_draft(
            session,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
        )
    return {"ok": True}


@router.post(
    "/sessions/{session_id}/answers/{order_index}/turns",
    summary="提交单题答案/补答或追问教练(SSE)",
)
async def submit_answer_turn(
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
) -> EventSourceResponse:
    sessionmaker = get_sessionmaker()
    return EventSourceResponse(
        interview_service.submit_answer_turn_sse(
            sessionmaker,
            session_id,
            order_index,
            payload,
        )
    )


@router.post(
    "/sessions/{session_id}/finish",
    summary="结束 M2.1 面试会话并生成总结(SSE)",
)
async def finish_session(session_id: int) -> EventSourceResponse:
    sessionmaker = get_sessionmaker()
    return EventSourceResponse(
        interview_service.finish_session_sse(sessionmaker, session_id)
    )


@router.post(
    "/sessions/{session_id}/submit",
    summary="提交答题会话并触发 Judge 评分(SSE)",
)
async def submit_session(session_id: int) -> EventSourceResponse:
    sessionmaker = get_sessionmaker()
    return EventSourceResponse(
        answer_service.submit_session_sse(sessionmaker, session_id)
    )


@router.get(
    "/sessions/{session_id}/recall",
    summary="下载 session 沉淀 markdown",
)
async def get_session_recall(session_id: int) -> PlainTextResponse:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        markdown = await answer_service.get_session_recall_markdown(session, session_id)
    return PlainTextResponse(markdown, media_type="text/markdown")


@router.post(
    "/sessions/{session_id}/abandon",
    summary="放弃答题会话",
)
async def abandon_session(session_id: int) -> dict:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        return await answer_service.abandon_session(session, session_id)
