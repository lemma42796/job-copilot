"""JDs router. ADR-0006 D4 / D12 + API_SPEC §6.3.

Endpoints:

- POST   /v1/jds/parse                 sync, returns JDParseResponse (201)
- POST   /v1/jds/parse?stream=1        SSE: started → result → done
                                         (failure: started → error → done)
- GET    /v1/jds                       JDListResponse (cursor-paginated)
- GET    /v1/jds/{id}                  JDDetail
- PATCH  /v1/jds/{id}                  JDDetail
- DELETE /v1/jds/{id}                  204

The SSE path uses `create_pending_jd` + `run_parse` directly so the
`started` event can carry `resource_id` (the freshly-inserted jd.id)
before Phase 2 begins. The sync path uses the `create_and_parse`
wrapper. See `services/jd_service.py` for the transaction model.
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
from jobcopilot_api.infra.llm import get_llm_client
from jobcopilot_api.infra.prompts import LoadedPrompt
from jobcopilot_api.infra.request_id import generate_uuid7
from jobcopilot_api.llm.client import LLMClient, LLMResult
from jobcopilot_api.models import Jd
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.jds import (
    JDDetail,
    JDListItem,
    JDListResponse,
    JDParseInput,
    JDParseResponse,
    JDPatchInput,
    JDSkill,
    JdSource,
    JdStatus,
    JDStructured,
    JDTokens,
)
from jobcopilot_api.services.jd_service import (
    create_and_parse,
    create_pending_jd,
    get_jd,
    list_jds,
    patch_jd,
    run_parse,
    soft_delete_jd,
)

router = APIRouter(tags=["jds"])

PROMPT_KEY: tuple[str, str] = ("jd_parser", "v1.0.1")


# ---------------------------------------------------------------------------
# Helpers: ORM <-> wire schema
# ---------------------------------------------------------------------------


def _skills(rows: list[Any]) -> list[JDSkill]:
    return [JDSkill.model_validate(r) for r in rows]


def _structured_from_jd(jd: Jd) -> JDStructured:
    """Reconstruct `JDStructured` from a parsed Jd row. Falls back to
    schema defaults for cells that the LLM didn't populate."""
    return JDStructured(
        company=jd.company,
        title=jd.title or "",
        location=jd.location,
        salary_min=jd.salary_min,
        salary_max=jd.salary_max,
        salary_period=jd.salary_period or "monthly",
        salary_months=jd.salary_months,
        job_level=jd.job_level,
        years_required=jd.years_required,
        education=jd.education,
        hard_skills=_skills(jd.hard_skills),
        soft_skills=_skills(jd.soft_skills),
        bonus_skills=_skills(jd.bonus_skills),
        responsibilities=list(jd.responsibilities),
        description=jd.description or "",
        confidence=float(jd.parse_confidence) if jd.parse_confidence is not None else 0.0,
    )


def _parse_response(jd: Jd, result: LLMResult) -> JDParseResponse:
    """Build the sync POST /parse response. Token breakdown comes from
    `LLMResult` because the Jd ORM only stores the total."""
    return JDParseResponse(
        id=jd.id,
        status=JdStatus(jd.status),
        structured=_structured_from_jd(jd),
        confidence=float(jd.parse_confidence) if jd.parse_confidence is not None else None,
        tokens=JDTokens(input=result.tokens_in, output=result.tokens_out),
        cost_cny=jd.parse_cost_cny,
    )


def _list_item(jd: Jd) -> JDListItem:
    return JDListItem(
        id=jd.id,
        source=JdSource(jd.source),
        status=JdStatus(jd.status),
        company=jd.company,
        title=jd.title,
        location=jd.location,
        salary_min=jd.salary_min,
        salary_max=jd.salary_max,
        salary_currency=jd.salary_currency,
        parse_confidence=jd.parse_confidence,
        created_at=jd.created_at,
    )


def _detail(jd: Jd) -> JDDetail:
    return JDDetail(
        id=jd.id,
        source=JdSource(jd.source),
        status=JdStatus(jd.status),
        raw_text=jd.raw_text,
        raw_file_id=jd.raw_file_id,
        company=jd.company,
        title=jd.title,
        location=jd.location,
        salary_min=jd.salary_min,
        salary_max=jd.salary_max,
        salary_currency=jd.salary_currency,
        salary_period=jd.salary_period,
        salary_months=jd.salary_months,
        job_level=jd.job_level,
        years_required=jd.years_required,
        education=jd.education,
        hard_skills=_skills(jd.hard_skills),
        soft_skills=_skills(jd.soft_skills),
        bonus_skills=_skills(jd.bonus_skills),
        responsibilities=list(jd.responsibilities),
        description=jd.description,
        parse_confidence=jd.parse_confidence,
        parse_model=jd.parse_model,
        parse_tokens=jd.parse_tokens,
        parse_cost_cny=jd.parse_cost_cny,
        created_at=jd.created_at,
        updated_at=jd.updated_at,
    )


def _resolve_prompt(request: Request) -> LoadedPrompt:
    """Pull the JDParser prompt from `app.state` (lifespan-loaded).

    Tests that drive the app through `ASGITransport` don't run lifespan,
    so they must seed `app.state.prompt_versions` themselves.
    """
    cache: dict[tuple[str, str], LoadedPrompt] | None = getattr(
        request.app.state, "prompt_versions", None
    )
    if cache is None or PROMPT_KEY not in cache:
        raise RuntimeError("prompt_versions cache missing — lifespan startup did not load prompts")
    return cache[PROMPT_KEY]


def _llm_dep() -> LLMClient:
    """Indirection so tests can override via `app.dependency_overrides`."""
    return get_llm_client()


def _sessionmaker_dep() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


# ---------------------------------------------------------------------------
# POST /v1/jds/parse
# ---------------------------------------------------------------------------


@router.post(
    "/jds/parse",
    response_model=None,
    responses={201: {"model": JDParseResponse}},
    status_code=status.HTTP_201_CREATED,
    summary="Parse a JD (sync default, ?stream=1 for SSE)",
)
async def parse(
    body: JDParseInput,
    request: Request,
    user_id: Annotated[int, Depends(current_user_id)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    sessionmaker_: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker_dep)],
    stream: Annotated[bool, Query()] = False,
) -> JDParseResponse | EventSourceResponse:
    prompt = _resolve_prompt(request)

    if stream:
        return EventSourceResponse(
            _sse_parse(
                sessionmaker=sessionmaker_,
                user_id=user_id,
                body=body,
                llm=llm,
                prompt=prompt,
            )
        )

    jd, result = await create_and_parse(
        sessionmaker_,
        user_id=user_id,
        source=body.source,
        text=body.text,
        file_id=body.file_id,
        llm=llm,
        prompt=prompt,
    )
    return _parse_response(jd, result)


async def _sse_parse(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    body: JDParseInput,
    llm: LLMClient,
    prompt: LoadedPrompt,
) -> AsyncIterator[dict[str, str]]:
    """Yield the 3-event SSE sequence for /v1/jds/parse?stream=1.

    `started` carries the freshly-inserted `resource_id`; on pre-insert
    failures (text too short / PDF garbage) we skip `started` and emit
    `error → done` directly. ADR-0006 D4 / API_SPEC §5.2.
    """
    job_id = generate_uuid7()
    try:
        jd_id, raw_text = await create_pending_jd(
            sessionmaker,
            user_id=user_id,
            source=body.source,
            text=body.text,
            file_id=body.file_id,
        )
    except JobCopilotError as exc:
        # Pre-insert failure: no row exists, no `started` to emit.
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("started", {"job_id": job_id, "resource_id": jd_id})

    try:
        await run_parse(
            sessionmaker,
            jd_id=jd_id,
            user_id=user_id,
            raw_text=raw_text,
            llm=llm,
            prompt=prompt,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("result", {"resource_id": jd_id, "url": f"/v1/jds/{jd_id}"})
    yield _sse("done", {"ok": True})


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# GET /v1/jds + GET /v1/jds/{id}
# ---------------------------------------------------------------------------


@router.get("/jds", response_model=JDListResponse, summary="List JDs (cursor-paginated)")
async def list_(
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[
        Literal["parsing", "parsed", "parse_failed"] | None, Query(alias="status")
    ] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JDListResponse:
    cursor_id = int(cursor) if cursor and cursor.isdigit() else None

    rows, has_more = await list_jds(
        session,
        user_id=user_id,
        status=status_filter,
        created_after=created_after,
        cursor=cursor_id,
        limit=limit,
    )
    next_cursor = str(rows[-1].id) if has_more and rows else None
    return JDListResponse(
        data=[_list_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/jds/{jd_id}", response_model=JDDetail, summary="Get a JD")
async def get(
    jd_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JDDetail:
    jd = await get_jd(session, user_id=user_id, jd_id=jd_id)
    return _detail(jd)


# ---------------------------------------------------------------------------
# PATCH + DELETE
# ---------------------------------------------------------------------------


@router.patch("/jds/{jd_id}", response_model=JDDetail, summary="Patch a JD")
async def patch(
    jd_id: int,
    patch_body: JDPatchInput,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JDDetail:
    async with session.begin():
        jd = await patch_jd(session, user_id=user_id, jd_id=jd_id, patch=patch_body)
    return _detail(jd)


@router.delete(
    "/jds/{jd_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a JD",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete(
    jd_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    async with session.begin():
        await soft_delete_jd(session, user_id=user_id, jd_id=jd_id)
