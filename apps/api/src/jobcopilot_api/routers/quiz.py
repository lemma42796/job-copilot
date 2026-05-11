"""Quiz REST + SSE 端点(M2,4-API_SPEC §4.1 / §4.6)。

router 自身 prefix `/quiz`;main.py include 时挂 `/api` 前缀,实际端点路径
= `/api/quiz/*`。M2 第 4 步只实现 POST /api/quiz/sessions(SSE 出题);
GET / PUT / submit / abandon 端点(§4.2-§4.5)挂账 M2 第 7 步答题流程。
"""

from __future__ import annotations

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from jobcopilot_api.errors import (
    ModeNotImplementedError,
    QueryRequiredError,
    QueryTooLongError,
)
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.schemas.quiz import QuizSessionCreateIn
from jobcopilot_api.services import quiz_service

router = APIRouter(tags=["quiz"], prefix="/quiz")

QUERY_MAX_LENGTH = 200


@router.post(
    "/sessions",
    summary="聊天框 query 出题(SSE)",
)
async def create_session(payload: QuizSessionCreateIn) -> EventSourceResponse:
    """4-API_SPEC §4.1。

    入参 `{query, mode, question_count, jd_ids?}`,SSE 流见 §4.6:
    started → progress(query_rewriting / hybrid / rerank / parent_doc /
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
