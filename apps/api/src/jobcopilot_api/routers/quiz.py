"""Quiz REST 端点(M2 / P0 / P3)。

router 自身 prefix `/quiz`;main.py include 时挂 `/api` 前缀,实际端点路径
= `/api/quiz/*`。

P0:每个端点都要 `CurrentUserId`,并把 user_id 透传到 service —— service 层
的每条查询都按它过滤,别人的 session 一律 404。

P3:四个原本直接返回 SSE 流的长任务端点(出题、答题回合、结束会话、提交
评分)改成 **202 + job_id**。在线请求只做三件事:校验入参、写一行 job
(必要时预占业务行)、推队列;一个 LLM 调用都不发生,连接立刻释放。
前端拿 job_id 去 `GET /api/jobs/{job_id}/stream` 订阅进度。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import PlainTextResponse

from jobcopilot_api.errors import (
    ModeNotImplementedError,
    QueryRequiredError,
    QueryTooLongError,
)
from jobcopilot_api.infra.auth import CurrentUserId
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.job import (
    KIND_ANSWER_TURN,
    KIND_QUIZ_CREATE,
    KIND_SESSION_FINISH,
    KIND_SESSION_SUBMIT,
)
from jobcopilot_api.schemas.job import JobAcceptedOut
from jobcopilot_api.schemas.quiz import (
    AnswerDraftIn,
    AnswerTurnSubmitIn,
    QuizSessionCreateIn,
    QuizSessionDetailOut,
    QuizSessionListOut,
)
from jobcopilot_api.services import (
    answer_service,
    job_service,
    quiz_service,
)

router = APIRouter(tags=["quiz"], prefix="/quiz")

QUERY_MAX_LENGTH = 200


@router.post(
    "/sessions",
    summary="聊天框 query 出题(异步,返回 job_id)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_session(
    payload: QuizSessionCreateIn,
    user_id: CurrentUserId,
) -> JobAcceptedOut:
    """校验 + 预占 quiz_sessions 行 + 入队,不调用 LLM。

    session 行在这里先建出来,前端拿到 202 就能跳转到会话页,再用 job_id
    订阅出题进度。
    """
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

    async with get_sessionmaker()() as session:
        await job_service.assert_queue_has_room(session)
        quiz_session = await quiz_service.create_quiz_session(
            session, payload, user_id=user_id
        )
        job = await job_service.enqueue(
            session,
            user_id=user_id,
            kind=KIND_QUIZ_CREATE,
            payload=payload.model_dump(mode="json")
            | {"session_id": quiz_session.id},
            resource_kind="quiz_session",
            resource_id=quiz_session.id,
            dedupe_key=f"quiz_create:{quiz_session.id}",
        )
        await session.commit()
        accepted = JobAcceptedOut(
            job_id=job.id,
            status=job.status,
            kind=job.kind,
            resource_kind="quiz_session",
            resource_id=quiz_session.id,
        )
    await job_service.publish(accepted.job_id)
    return accepted


@router.get("/sessions", summary="查询最近答题会话")
async def list_sessions(
    user_id: CurrentUserId,
    status_filter: Literal["in_progress", "submitted", "abandoned"] | None = Query(
        default=None, alias="status"
    ),
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> QuizSessionListOut:
    async with get_sessionmaker()() as session:
        return await answer_service.list_sessions(
            session,
            status=status_filter,
            cursor=cursor,
            limit=limit,
            user_id=user_id,
        )


@router.get("/sessions/{session_id}", summary="查询答题会话详情")
async def get_session(
    session_id: int, user_id: CurrentUserId
) -> QuizSessionDetailOut:
    async with get_sessionmaker()() as session:
        return await answer_service.get_session_detail(
            session, session_id, user_id=user_id
        )


@router.put(
    "/sessions/{session_id}/answers/{order_index}",
    summary="保存单题答案草稿",
)
async def save_answer(
    session_id: int,
    order_index: int,
    payload: AnswerDraftIn,
    user_id: CurrentUserId,
) -> dict[str, bool]:
    async with get_sessionmaker()() as session:
        await answer_service.save_draft(
            session,
            session_id=session_id,
            order_index=order_index,
            payload=payload,
            user_id=user_id,
        )
    return {"ok": True}


@router.post(
    "/sessions/{session_id}/answers/{order_index}/turns",
    summary="提交单题答案/补答或追问教练(异步,返回 job_id)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_answer_turn(
    session_id: int,
    order_index: int,
    payload: AnswerTurnSubmitIn,
    user_id: CurrentUserId,
) -> JobAcceptedOut:
    async with get_sessionmaker()() as session:
        await job_service.assert_queue_has_room(session)
        # 归属校验放在入队前:别人的 session 直接 404,不会白占一行 job。
        await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        job = await job_service.enqueue(
            session,
            user_id=user_id,
            kind=KIND_ANSWER_TURN,
            payload={
                "session_id": session_id,
                "order_index": order_index,
                "body": payload.model_dump(mode="json"),
            },
            resource_kind="quiz_session",
            resource_id=session_id,
        )
        await session.commit()
        accepted = _accepted(job, "quiz_session", session_id)
    await job_service.publish(accepted.job_id)
    return accepted


@router.post(
    "/sessions/{session_id}/finish",
    summary="结束面试会话并生成总结(异步,返回 job_id)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def finish_session(
    session_id: int, user_id: CurrentUserId
) -> JobAcceptedOut:
    async with get_sessionmaker()() as session:
        await job_service.assert_queue_has_room(session)
        await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        job = await job_service.enqueue(
            session,
            user_id=user_id,
            kind=KIND_SESSION_FINISH,
            payload={"session_id": session_id},
            resource_kind="quiz_session",
            resource_id=session_id,
            # 同一个会话只允许有一个未完成的结束任务,连点两次不会跑两遍。
            dedupe_key=f"session_finish:{session_id}",
        )
        await session.commit()
        accepted = _accepted(job, "quiz_session", session_id)
    await job_service.publish(accepted.job_id)
    return accepted


@router.post(
    "/sessions/{session_id}/submit",
    summary="提交答题会话并触发 Judge 评分(异步,返回 job_id)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_session(
    session_id: int, user_id: CurrentUserId
) -> JobAcceptedOut:
    async with get_sessionmaker()() as session:
        await job_service.assert_queue_has_room(session)
        await answer_service.load_owned_session(
            session, session_id, user_id=user_id
        )
        job = await job_service.enqueue(
            session,
            user_id=user_id,
            kind=KIND_SESSION_SUBMIT,
            payload={"session_id": session_id},
            resource_kind="quiz_session",
            resource_id=session_id,
            dedupe_key=f"session_submit:{session_id}",
        )
        await session.commit()
        accepted = _accepted(job, "quiz_session", session_id)
    await job_service.publish(accepted.job_id)
    return accepted


@router.get(
    "/sessions/{session_id}/recall",
    summary="下载 session 沉淀 markdown",
)
async def get_session_recall(
    session_id: int, user_id: CurrentUserId
) -> Response:
    async with get_sessionmaker()() as session:
        markdown = await answer_service.get_session_recall_markdown(
            session, session_id, user_id=user_id
        )
    return PlainTextResponse(markdown, media_type="text/markdown")


@router.post("/sessions/{session_id}/abandon", summary="放弃答题会话")
async def abandon_session(session_id: int, user_id: CurrentUserId) -> dict:
    async with get_sessionmaker()() as session:
        return await answer_service.abandon_session(
            session, session_id, user_id=user_id
        )


def _accepted(job, resource_kind: str, resource_id: int) -> JobAcceptedOut:
    return JobAcceptedOut(
        job_id=job.id,
        status=job.status,
        kind=job.kind,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )
