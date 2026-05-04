"""Matches router. API_SPEC §6.5 + AGENT_DESIGN §6.

Endpoints:

- POST   /v1/matches                  SSE: started → result → done
                                         (pre-insert / analyze failure: started?
                                          → error → done — see _sse_match docstring)
- GET    /v1/matches                  MatchListResponse (cursor)
- GET    /v1/matches/{id}             MatchDetail
- DELETE /v1/matches/{id}             204

POST 强制 SSE(API_SPEC §6.5):匹配是 retrieve + LLM 链路,P95 ~10s+,
sync 等不起。Sync 不另开 alias — 客户端拿 `result.url` 走 GET 取详情。

SSE shape 与 jds.py 对称:`started` 在 phase-1 INSERT 之后 emit 带
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
from jobcopilot_api.models import Match
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.matches import (
    MatchCreateInput,
    MatchDetail,
    MatchedSkill,
    MatchListItem,
    MatchListResponse,
    MatchResult,
    MatchStatus,
    MatchTokens,
    MissingSkill,
)
from jobcopilot_api.services.match_service import (
    create_pending_match,
    get_match,
    list_matches,
    run_analyze,
    soft_delete_match,
)

router = APIRouter(tags=["matches"])

PROMPT_KEY: tuple[str, str] = ("match_analyst", "v1.0.0")


# ---------------------------------------------------------------------------
# Helpers: ORM <-> wire schema
# ---------------------------------------------------------------------------


def _matched_skills(rows: list[Any]) -> list[MatchedSkill]:
    return [MatchedSkill.model_validate(r) for r in rows]


def _missing_skills(rows: list[Any]) -> list[MissingSkill]:
    return [MissingSkill.model_validate(r) for r in rows]


def _structured_from_match(match: Match) -> MatchResult | None:
    """Reconstruct MatchResult from a scored row;pending / failed → None。"""
    if match.status != "scored" or match.score is None:
        return None
    return MatchResult(
        score=match.score,
        matched_skills=_matched_skills(match.matched_skills),
        missing_skills=_missing_skills(match.missing_skills),
        advantage_summary=match.advantage_summary or "",
        gap_summary=match.gap_summary or "",
        suggestions=list(match.suggestions),
    )


def _list_item(match: Match) -> MatchListItem:
    return MatchListItem(
        id=match.id,
        status=MatchStatus(match.status),
        jd_id=match.jd_id,
        profile_id=match.profile_id,
        score=match.score,
        matched_skills_count=len(match.matched_skills) if match.matched_skills else 0,
        missing_skills_count=len(match.missing_skills) if match.missing_skills else 0,
        created_at=match.created_at,
    )


def _detail(match: Match) -> MatchDetail:
    tokens = (
        MatchTokens(input=match.tokens_in, output=match.tokens_out)
        if match.tokens_in is not None and match.tokens_out is not None
        else None
    )
    return MatchDetail(
        id=match.id,
        status=MatchStatus(match.status),
        jd_id=match.jd_id,
        profile_id=match.profile_id,
        structured=_structured_from_match(match),
        model=match.model,
        tokens=tokens,
        cost_cny=match.cost_cny,
        latency_ms=match.latency_ms,
        created_at=match.created_at,
        updated_at=match.updated_at,
    )


def _resolve_prompt(request: Request) -> LoadedPrompt:
    cache: dict[tuple[str, str], LoadedPrompt] | None = getattr(
        request.app.state, "prompt_versions", None
    )
    if cache is None or PROMPT_KEY not in cache:
        raise RuntimeError("prompt_versions cache missing — lifespan startup did not load prompts")
    return cache[PROMPT_KEY]


def _llm_dep() -> LLMClient:
    return get_llm_client()


def _sessionmaker_dep() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


def _embedder_dep() -> Embedder:
    return get_embedder()


# ---------------------------------------------------------------------------
# POST /v1/matches — SSE only
# ---------------------------------------------------------------------------


@router.post(
    "/matches",
    response_model=None,
    responses={200: {"description": "SSE stream"}},
    summary="Create a match analysis (SSE only)",
)
async def create(
    body: MatchCreateInput,
    request: Request,
    user_id: Annotated[int, Depends(current_user_id)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    sessionmaker_: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker_dep)],
    embedder: Annotated[Embedder, Depends(_embedder_dep)],
) -> EventSourceResponse:
    prompt = _resolve_prompt(request)
    return EventSourceResponse(
        _sse_match(
            sessionmaker=sessionmaker_,
            user_id=user_id,
            body=body,
            llm=llm,
            embedder=embedder,
            prompt=prompt,
        )
    )


async def _sse_match(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    body: MatchCreateInput,
    llm: LLMClient,
    embedder: Embedder,
    prompt: LoadedPrompt,
) -> AsyncIterator[dict[str, str]]:
    """SSE event sequence for /v1/matches。

    Pre-insert 失败(JD 不存在 / 未 parsed / profile 无 chunks)→ 没有
    `started`(没 resource_id 可发),直接 `error → done(ok=false)`。

    Phase-1 成功后 emit `started`,接 phase-2 run_analyze。run_analyze
    自己处理 mark_failed,raise 出来后 SSE emit `error → done(ok=false)`。
    成功 emit `result {resource_id, url, score}` + `done(ok=true)`。"""
    job_id = generate_uuid7()
    try:
        match_id = await create_pending_match(
            sessionmaker, user_id=user_id, jd_id=body.jd_id, profile_id=body.profile_id
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("started", {"job_id": job_id, "resource_id": match_id})

    try:
        match_row, _llm_result, _retrieve = await run_analyze(
            sessionmaker,
            match_id=match_id,
            user_id=user_id,
            embedder=embedder,
            llm=llm,
            prompt=prompt,
            trace_id=job_id,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse(
        "result",
        {
            "resource_id": match_id,
            "url": f"/v1/matches/{match_id}",
            "score": match_row.score,
        },
    )
    yield _sse("done", {"ok": True})


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# GET /v1/matches + GET /v1/matches/{id}
# ---------------------------------------------------------------------------


@router.get("/matches", response_model=MatchListResponse, summary="List matches (cursor-paginated)")
async def list_(
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    jd_id: Annotated[int | None, Query()] = None,
    profile_id: Annotated[int | None, Query()] = None,
    status_filter: Annotated[
        Literal["pending", "scored", "failed"] | None, Query(alias="status")
    ] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MatchListResponse:
    cursor_id = int(cursor) if cursor and cursor.isdigit() else None

    rows, has_more = await list_matches(
        session,
        user_id=user_id,
        jd_id=jd_id,
        profile_id=profile_id,
        status=status_filter,
        created_after=created_after,
        cursor=cursor_id,
        limit=limit,
    )
    next_cursor = str(rows[-1].id) if has_more and rows else None
    return MatchListResponse(
        data=[_list_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/matches/{match_id}", response_model=MatchDetail, summary="Get a match")
async def get(
    match_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MatchDetail:
    match = await get_match(session, user_id=user_id, match_id=match_id)
    return _detail(match)


@router.delete(
    "/matches/{match_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a match",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete(
    match_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    async with session.begin():
        await soft_delete_match(session, user_id=user_id, match_id=match_id)
