"""Resumes router. API_SPEC §6.6 + AGENT_DESIGN §7。

Endpoints:

- POST   /v1/resumes/generate            SSE: started → node_completed* → result → done
                                            (pre-insert / generate failure: started?
                                             → error → done — see _sse_resume docstring)
- GET    /v1/resumes                     ResumeListResponse (cursor)
- GET    /v1/resumes/{id}                ResumeDetail
- GET    /v1/resumes/{id}/versions       全部版本(W8)
- POST   /v1/resumes/{id}/versions       用户编辑后保存(W8 — body {markdown, note?})
- DELETE /v1/resumes/{id}                204

POST 强制 SSE(API_SPEC §6.6):简历定制是 retrieve + planner + drafter
+ reviewer (+ optional revise) 4-5 次 LLM 调用,P95 ~60-120s,sync 等不起。

SSE shape(M3 W7 升级):`started` 在 phase-1 INSERT 之后 emit 带
`resource_id`(永久约束 #4);`node_completed` 在 5 节点 graph 每步完成
后 emit 给前端进度条用;`result` 在 graph END 后 emit 最终态。

`node_completed` data:`{"node": "retrieve|plan|draft|review|revise",
"revision_count": int}`。前端可按节点完成数量驱动进度条;revision_count
反映 reviewer 失败后 revise 节点重跑次数(0 = 首次,1+ = revise)。

W8 新增 `drafter_token` 事件:`{"phase": "draft|revise", "delta": str}`。
drafter / revise 节点跑 LLM 时按 token chunk 流式发出,front-end 累积成
预览;phase 切换是前端重置缓冲的信号(revise 重写整篇,不要追加)。
其余节点(retrieve / plan / review)无流式价值,不发该事件。

永久约束 #21 前端走 `lib/sse.ts`,不用 EventSource。
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
from jobcopilot_api.models import Resume, ResumeVersion
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.resumes import (
    ResumeCreateInput,
    ResumeDetail,
    ResumeListItem,
    ResumeListResponse,
    ResumeStatus,
    ResumeTokens,
    ResumeVersionCreateInput,
    ResumeVersionItem,
    ResumeVersionListResponse,
    ReviewFinding,
)
from jobcopilot_api.services.resume_service import (
    create_pending_resume,
    create_resume_version,
    get_resume,
    list_resume_versions,
    list_resumes,
    run_generate_stream,
    soft_delete_resume,
)

router = APIRouter(tags=["resumes"])

DRAFTER_PROMPT_KEY: tuple[str, str] = ("resume_drafter", "v1.0.6")
REVIEWER_PROMPT_KEY: tuple[str, str] = ("resume_reviewer", "v1.0.3")
PLANNER_PROMPT_KEY: tuple[str, str] = ("resume_planner", "v1.0.0")


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


def _version_item(v: ResumeVersion) -> ResumeVersionItem:
    return ResumeVersionItem(
        id=v.id,
        version_number=v.version_number,
        markdown=v.markdown,
        edit_type=v.edit_type,  # type: ignore[arg-type]  # Literal narrowing on free-form col
        edit_note=v.edit_note,
        created_at=v.created_at,
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
    planner_prompt = _resolve_prompt(request, PLANNER_PROMPT_KEY)
    return EventSourceResponse(
        _sse_resume(
            sessionmaker=sessionmaker_,
            user_id=user_id,
            body=body,
            llm=llm,
            embedder=embedder,
            drafter_prompt=drafter_prompt,
            reviewer_prompt=reviewer_prompt,
            planner_prompt=planner_prompt,
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
    planner_prompt: LoadedPrompt,
) -> AsyncIterator[dict[str, str]]:
    """SSE event sequence for /v1/resumes/generate(M3 W7 graph 升级)。

    Pre-insert 失败(JD 不存在 / 未 parsed / profile 无 chunks / match 不
    一致)→ 没有 `started`(没 resource_id 可发),直接 `error → done(ok=false)`。

    Phase-1 成功后 emit `started`,接 phase-2 run_generate_stream。每个 graph
    节点完成 emit 一条 `node_completed`(retrieve / plan / draft / review /
    revise);最后 emit `result + done(ok=true)`。

    run_generate_stream 内部已 mark_failed + raise,捕获后 emit
    `error → done(ok=false)`。review_failed 也算 ok=true(markdown 仍展示)。"""
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
        async for event in run_generate_stream(
            sessionmaker,
            resume_id=resume_id,
            user_id=user_id,
            embedder=embedder,
            llm=llm,
            drafter_prompt=drafter_prompt,
            reviewer_prompt=reviewer_prompt,
            planner_prompt=planner_prompt,
            trace_id=job_id,
        ):
            kind = event.get("event")
            if kind == "drafter_token":
                yield _sse(
                    "drafter_token",
                    {
                        "phase": event["phase"],
                        "delta": event["delta"],
                    },
                )
            elif kind == "node_completed":
                yield _sse(
                    "node_completed",
                    {
                        "node": event["node"],
                        "revision_count": event["revision_count"],
                    },
                )
            elif kind == "final":
                yield _sse(
                    "result",
                    {
                        "resource_id": resume_id,
                        "url": f"/v1/resumes/{resume_id}",
                        "status": event["status"],
                        "review_passed": event["review_passed"],
                        "revisions": event.get("revisions", 0),
                    },
                )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

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


# ---------------------------------------------------------------------------
# Versions(W8)
# ---------------------------------------------------------------------------


@router.get(
    "/resumes/{resume_id}/versions",
    response_model=ResumeVersionListResponse,
    summary="List versions of a resume",
)
async def list_versions(
    resume_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResumeVersionListResponse:
    rows = await list_resume_versions(session, user_id=user_id, resume_id=resume_id)
    return ResumeVersionListResponse(data=[_version_item(r) for r in rows])


@router.post(
    "/resumes/{resume_id}/versions",
    response_model=ResumeVersionItem,
    status_code=status.HTTP_201_CREATED,
    summary="Save edited resume markdown as a new version",
)
async def create_version(
    resume_id: int,
    body: ResumeVersionCreateInput,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResumeVersionItem:
    async with session.begin():
        version = await create_resume_version(
            session,
            user_id=user_id,
            resume_id=resume_id,
            markdown=body.markdown,
            note=body.note,
        )
    return _version_item(version)
