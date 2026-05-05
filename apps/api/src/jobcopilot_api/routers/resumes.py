"""Resumes router. API_SPEC §6.6 + AGENT_DESIGN §7。

Endpoints:

- POST   /v1/resumes/generate         SSE: started → result → done
                                         (pre-insert / generate failure: started?
                                          → error → done — see _sse_resume docstring)
- GET    /v1/resumes                  ResumeListResponse (cursor)
- GET    /v1/resumes/{id}             ResumeDetail
- DELETE /v1/resumes/{id}             204

POST 强制 SSE(API_SPEC §6.6):简历定制是 retrieve + drafter + reviewer
两 LLM 串调,P95 ~30-90s,sync 等不起。MVP 不发 node-level 进度事件,
保持与 matches 同 shape(started → result → done);node_started /
node_completed / token streaming 留 v1.1。

SSE shape 与 matches.py 对称:`started` 在 phase-1 INSERT 之后 emit 带
`resource_id`(永久约束 #4)。永久约束 #21 前端走 `lib/sse.ts`,不用
EventSource。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.infra.db import get_session, get_sessionmaker
from jobcopilot_api.infra.embedder import get_embedder
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.infra.request_id import generate_uuid7
from jobcopilot_api.llm.client import LLMClient
from jobcopilot_api.llm.embedders import Embedder
from jobcopilot_api.models import Resume
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.resumes import (
    ResumeCreateInput,
    ResumeDetail,
    ResumeListItem,
    ResumeListResponse,
    ResumeStatus,
    ResumeTokens,
    ReviewFinding,
)
from jobcopilot_api.services.resume_service import (
    create_pending_resume,
    get_resume,
    list_resumes,
    run_generate,
    soft_delete_resume,
)

router = APIRouter(tags=["resumes"])

DRAFTER_PROMPT_KEY: tuple[str, str] = ("resume_drafter", "v1.0.3")
REVIEWER_PROMPT_KEY: tuple[str, str] = ("resume_reviewer", "v1.0.2")


# ---------------------------------------------------------------------------
# Helpers: ORM <-> wire schema
# ---------------------------------------------------------------------------


def _review_findings(rows: list[Any]) -> list[ReviewFinding]:
    return [ReviewFinding.model_validate(r) for r in rows]


def _list_item(resume: Resume) -> ResumeListItem:
    return ResumeListItem(
        id=resume.id,
        status=ResumeStatus(resume.status),
        jd_id=resume.jd_id,
        profile_id=resume.profile_id,
        match_id=resume.match_id,
        title=resume.title,
        review_passed=resume.review_passed,
        review_findings_count=len(resume.review_findings) if resume.review_findings else 0,
        cost_cny=resume.cost_cny,
        created_at=resume.created_at,
    )


def _detail(resume: Resume) -> ResumeDetail:
    tokens = (
        ResumeTokens(input=resume.tokens_in, output=resume.tokens_out)
        if resume.tokens_in is not None and resume.tokens_out is not None
        else None
    )
    return ResumeDetail(
        id=resume.id,
        status=ResumeStatus(resume.status),
        jd_id=resume.jd_id,
        profile_id=resume.profile_id,
        match_id=resume.match_id,
        title=resume.title,
        markdown=resume.markdown,
        review_passed=resume.review_passed,
        review_findings=_review_findings(resume.review_findings),
        generation_model=resume.generation_model,
        review_model=resume.review_model,
        tokens=tokens,
        cost_cny=resume.cost_cny,
        latency_ms=resume.latency_ms,
        created_at=resume.created_at,
        updated_at=resume.updated_at,
    )


def _resolve_prompt(request: Request, key: tuple[str, str]) -> LoadedPrompt:
    cache: dict[tuple[str, str], LoadedPrompt] | None = getattr(
        request.app.state, "prompt_versions", None
    )
    if cache is None or key not in cache:
        raise RuntimeError(
            f"prompt_versions cache missing {key} — lifespan startup did not load prompts"
        )
    return cache[key]


def _llm_dep() -> LLMClient:
    return get_llm_client()


def _sessionmaker_dep() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


def _embedder_dep() -> Embedder:
    return get_embedder()


# ---------------------------------------------------------------------------
# POST /v1/resumes/generate — SSE only
# ---------------------------------------------------------------------------


@router.post(
    "/resumes/generate",
    response_model=None,
    responses={200: {"description": "SSE stream"}},
    summary="Generate a tailored resume (SSE only)",
)
async def generate(
    body: ResumeCreateInput,
    request: Request,
    user_id: Annotated[int, Depends(current_user_id)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    sessionmaker_: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker_dep)],
    embedder: Annotated[Embedder, Depends(_embedder_dep)],
) -> EventSourceResponse:
    drafter_prompt = _resolve_prompt(request, DRAFTER_PROMPT_KEY)
    reviewer_prompt = _resolve_prompt(request, REVIEWER_PROMPT_KEY)
    return EventSourceResponse(
        _sse_resume(
            sessionmaker=sessionmaker_,
            user_id=user_id,
            body=body,
            llm=llm,
            embedder=embedder,
            drafter_prompt=drafter_prompt,
            reviewer_prompt=reviewer_prompt,
        )
    )


async def _sse_resume(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    body: ResumeCreateInput,
    llm: LLMClient,
    embedder: Embedder,
    drafter_prompt: LoadedPrompt,
    reviewer_prompt: LoadedPrompt,
) -> AsyncIterator[dict[str, str]]:
    """SSE event sequence for /v1/resumes/generate。

    Pre-insert 失败(JD 不存在 / 未 parsed / profile 无 chunks / match 不
    一致)→ 没有 `started`(没 resource_id 可发),直接 `error → done(ok=false)`。

    Phase-1 成功后 emit `started`,接 phase-2 run_generate。run_generate
    自己处理 mark_failed,raise 出来后 SSE emit `error → done(ok=false)`。
    成功(包括 review_failed)emit `result {resource_id, url, status, review_passed}`
    + `done(ok=true)`。review_failed 也算 ok=true(markdown 仍展示)。"""
    job_id = generate_uuid7()
    try:
        resume_id = await create_pending_resume(
            sessionmaker,
            user_id=user_id,
            jd_id=body.jd_id,
            profile_id=body.profile_id,
            match_id=body.match_id,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("started", {"job_id": job_id, "resource_id": resume_id})

    try:
        resume_row, _drafter, _reviewer, _retrieve = await run_generate(
            sessionmaker,
            resume_id=resume_id,
            user_id=user_id,
            embedder=embedder,
            llm=llm,
            drafter_prompt=drafter_prompt,
            reviewer_prompt=reviewer_prompt,
            trace_id=job_id,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse(
        "result",
        {
            "resource_id": resume_id,
            "url": f"/v1/resumes/{resume_id}",
            "status": resume_row.status,
            "review_passed": resume_row.review_passed,
        },
    )
    yield _sse("done", {"ok": True})


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# GET /v1/resumes + GET /v1/resumes/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/resumes",
    response_model=ResumeListResponse,
    summary="List resumes (cursor-paginated)",
)
async def list_(
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    jd_id: Annotated[int | None, Query()] = None,
    profile_id: Annotated[int | None, Query()] = None,
    match_id: Annotated[int | None, Query()] = None,
    status_filter: Annotated[
        Literal["generating", "review_failed", "ready", "failed"] | None,
        Query(alias="status"),
    ] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResumeListResponse:
    cursor_id = int(cursor) if cursor and cursor.isdigit() else None

    rows, has_more = await list_resumes(
        session,
        user_id=user_id,
        jd_id=jd_id,
        profile_id=profile_id,
        match_id=match_id,
        status=status_filter,
        created_after=created_after,
        cursor=cursor_id,
        limit=limit,
    )
    next_cursor = str(rows[-1].id) if has_more and rows else None
    return ResumeListResponse(
        data=[_list_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/resumes/{resume_id}", response_model=ResumeDetail, summary="Get a resume")
async def get(
    resume_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResumeDetail:
    resume = await get_resume(session, user_id=user_id, resume_id=resume_id)
    return _detail(resume)


@router.delete(
    "/resumes/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a resume",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete(
    resume_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    async with session.begin():
        await soft_delete_resume(session, user_id=user_id, resume_id=resume_id)
