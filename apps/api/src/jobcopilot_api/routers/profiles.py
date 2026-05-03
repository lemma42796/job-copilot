"""Profiles router. Mirrors `routers/jds` shape, with two M1 deviations:

- POST /v1/profiles/parse is **SSE-only** (resumes are long; sync mode
  doesn't earn its weight here). API_SPEC §6.4.
- GET /v1/profiles/{id} embeds the 4 child collections so the client
  doesn't need a follow-up request to render the detail view.

Endpoints:

- POST   /v1/profiles/parse            SSE: started → chunking_embedding → result → done
                                         (LLM fail: started → error → done;
                                          chunk fail: started → chunking_embedding{ok:false}
                                          → result → done — parse already committed)
- GET    /v1/profiles                  ProfileListResponse (cursor)
- GET    /v1/profiles/{id}             ProfileDetail (with structured)
- PATCH  /v1/profiles/{id}             ProfileDetail
- DELETE /v1/profiles/{id}             204
- POST   /v1/profiles/{id}/rechunk     SSE: started → chunking_embedding → done
- GET    /v1/profiles/{id}/chunks      ProfileChunksResponse (no embedding payload)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import sqlalchemy as sa
import structlog
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
from jobcopilot_api.models import (
    Profile,
    ProfileChunk,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
)
from jobcopilot_api.routers._deps import current_user_id
from jobcopilot_api.schemas.profiles import (
    ProfileChunkItem,
    ProfileChunksResponse,
    ProfileDetail,
    ProfileEducationItem,
    ProfileExperienceItem,
    ProfileListItem,
    ProfileListResponse,
    ProfileParseInput,
    ProfilePatchInput,
    ProfileProjectItem,
    ProfileSkillItem,
    ProfileSource,
    ProfileStats,
    ProfileStatus,
    ProfileStructured,
)
from jobcopilot_api.services.chunk_service import RebuildResult, rebuild_for_profile
from jobcopilot_api.services.profile_service import (
    create_pending_profile,
    get_children,
    get_profile,
    list_profiles,
    patch_profile,
    run_parse,
    soft_delete_profile,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["profiles"])

PROMPT_KEY: tuple[str, str] = ("profile_parser", "v1.0.0")


# ---------------------------------------------------------------------------
# Helpers: ORM <-> wire schema
# ---------------------------------------------------------------------------


def _experience_item(row: ProfileExperience) -> ProfileExperienceItem:
    return ProfileExperienceItem(
        company=row.company,
        title=row.title,
        location=row.location,
        start_date=row.start_date,
        end_date=row.end_date,
        is_current=row.is_current,
        description=row.description,
        bullets=list(row.bullets),
        tech_stack=list(row.tech_stack),
        achievements=list(row.achievements),
    )


def _project_item(row: ProfileProject) -> ProfileProjectItem:
    return ProfileProjectItem(
        name=row.name,
        role=row.role,
        start_date=row.start_date,
        end_date=row.end_date,
        description=row.description,
        bullets=list(row.bullets),
        tech_stack=list(row.tech_stack),
        achievements=list(row.achievements),
        repo_url=row.repo_url,
        demo_url=row.demo_url,
    )


def _skill_item(row: ProfileSkill) -> ProfileSkillItem:
    category = (
        row.category
        if row.category in {"language", "framework", "tool", "database", "cloud", "other"}
        else "other"
    )
    level = row.level if row.level in {"beginner", "intermediate", "advanced", "expert"} else None
    return ProfileSkillItem(
        name=row.name,
        name_raw=row.name_raw or row.name,
        category=category,
        level=level,
        years=float(row.years) if row.years is not None else None,
    )


def _education_item(row: ProfileEducation) -> ProfileEducationItem:
    return ProfileEducationItem(
        school=row.school,
        degree=row.degree,
        major=row.major,
        start_date=row.start_date,
        end_date=row.end_date,
        gpa=float(row.gpa) if row.gpa is not None else None,
        honors=list(row.honors),
    )


def _structured_from_rows(
    profile: Profile,
    exps: list[ProfileExperience],
    projs: list[ProfileProject],
    skills: list[ProfileSkill],
    edus: list[ProfileEducation],
) -> ProfileStructured:
    return ProfileStructured(
        full_name=profile.full_name,
        phone=profile.phone,
        email=profile.email,
        location=profile.location,
        summary=profile.summary,
        target_titles=list(profile.target_titles),
        experiences=[_experience_item(r) for r in exps],
        projects=[_project_item(r) for r in projs],
        skills=[_skill_item(r) for r in skills],
        educations=[_education_item(r) for r in edus],
    )


def _list_item(profile: Profile) -> ProfileListItem:
    return ProfileListItem(
        id=profile.id,
        source=ProfileSource(profile.source),
        status=ProfileStatus(profile.status),
        full_name=profile.full_name,
        location=profile.location,
        parse_model=profile.parse_model,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def _detail(session: AsyncSession, profile: Profile) -> ProfileDetail:
    exps, projs, skills, edus = await get_children(session, profile_id=profile.id)
    structured = _structured_from_rows(profile, exps, projs, skills, edus)
    return ProfileDetail(
        id=profile.id,
        source=ProfileSource(profile.source),
        status=ProfileStatus(profile.status),
        raw_text=profile.raw_text,
        raw_file_id=profile.raw_file_id,
        structured=structured,
        target_salary_min=profile.target_salary_min,
        target_salary_max=profile.target_salary_max,
        parse_model=profile.parse_model,
        parse_tokens=profile.parse_tokens,
        parse_cost_cny=profile.parse_cost_cny,
        stats=ProfileStats(
            experiences=len(exps),
            projects=len(projs),
            skills=len(skills),
            educations=len(edus),
        ),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
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
# POST /v1/profiles/parse — SSE only
# ---------------------------------------------------------------------------


@router.post(
    "/profiles/parse",
    response_model=None,
    responses={200: {"description": "SSE stream"}},
    summary="Parse a profile (SSE only)",
)
async def parse(
    body: ProfileParseInput,
    request: Request,
    user_id: Annotated[int, Depends(current_user_id)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    sessionmaker_: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker_dep)],
    embedder: Annotated[Embedder, Depends(_embedder_dep)],
) -> EventSourceResponse:
    prompt = _resolve_prompt(request)
    return EventSourceResponse(
        _sse_parse(
            sessionmaker=sessionmaker_,
            user_id=user_id,
            body=body,
            llm=llm,
            prompt=prompt,
            embedder=embedder,
        )
    )


async def _sse_parse(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    body: ProfileParseInput,
    llm: LLMClient,
    prompt: LoadedPrompt,
    embedder: Embedder,
) -> AsyncIterator[dict[str, str]]:
    """Yield the SSE sequence for /v1/profiles/parse.

    Same pre-insert failure handling as JD (skip `started`, emit
    `error → done`). After `run_parse` succeeds, kick off
    `rebuild_for_profile` and emit `chunking_embedding`. Chunking
    failure does NOT roll back the parse — the profile row is already
    `parsed` in DB; the caller can hit `POST /rechunk` later. We emit
    `chunking_embedding{ok:false}` so the client can surface a banner.
    """
    job_id = generate_uuid7()
    try:
        profile_id, raw_text = await create_pending_profile(
            sessionmaker,
            user_id=user_id,
            source=body.source,
            text=body.text,
            file_id=body.file_id,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("started", {"job_id": job_id, "resource_id": profile_id})

    try:
        await run_parse(
            sessionmaker,
            profile_id=profile_id,
            user_id=user_id,
            raw_text=raw_text,
            llm=llm,
            prompt=prompt,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse(
        "chunking_embedding",
        await _try_rebuild_payload(
            sessionmaker=sessionmaker,
            user_id=user_id,
            profile_id=profile_id,
            embedder=embedder,
            trace_id=job_id,
        ),
    )

    yield _sse("result", {"resource_id": profile_id, "url": f"/v1/profiles/{profile_id}"})
    yield _sse("done", {"ok": True})


async def _try_rebuild_payload(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    profile_id: int,
    embedder: Embedder,
    trace_id: str,
) -> dict[str, Any]:
    """Best-effort rebuild for the parse SSE tail. Returns the
    `chunking_embedding` event payload; never raises."""
    try:
        result = await rebuild_for_profile(
            sessionmaker,
            user_id=user_id,
            profile_id=profile_id,
            embedder=embedder,
            trace_id=trace_id,
        )
    except Exception as exc:
        log.warning(
            "profile_parse.chunk_failed",
            profile_id=profile_id,
            user_id=user_id,
            error=str(exc),
            trace_id=trace_id,
        )
        return {"ok": False, "error": str(exc)}
    return _rebuild_payload(result)


def _rebuild_payload(result: RebuildResult) -> dict[str, Any]:
    return {
        "ok": True,
        "chunks_written": result.chunks_written,
        "embed_model": result.embed_model,
        "tokens_in": result.tokens_in,
        "cost_cny": str(result.cost_cny),
    }


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# GET /v1/profiles + GET /v1/profiles/{id}
# ---------------------------------------------------------------------------


@router.get("/profiles", response_model=ProfileListResponse, summary="List profiles")
async def list_(
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProfileListResponse:
    cursor_id = int(cursor) if cursor and cursor.isdigit() else None

    rows, has_more = await list_profiles(session, user_id=user_id, cursor=cursor_id, limit=limit)
    next_cursor = str(rows[-1].id) if has_more and rows else None
    return ProfileListResponse(
        data=[_list_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/profiles/{profile_id}", response_model=ProfileDetail, summary="Get a profile")
async def get(
    profile_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileDetail:
    profile = await get_profile(session, user_id=user_id, profile_id=profile_id)
    return await _detail(session, profile)


# ---------------------------------------------------------------------------
# PATCH + DELETE
# ---------------------------------------------------------------------------


@router.patch("/profiles/{profile_id}", response_model=ProfileDetail, summary="Patch a profile")
async def patch(
    profile_id: int,
    patch_body: ProfilePatchInput,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileDetail:
    async with session.begin():
        profile = await patch_profile(
            session, user_id=user_id, profile_id=profile_id, patch=patch_body
        )
    return await _detail(session, profile)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a profile",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete(
    profile_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    async with session.begin():
        await soft_delete_profile(session, user_id=user_id, profile_id=profile_id)


# ---------------------------------------------------------------------------
# POST /v1/profiles/{id}/rechunk — SSE
# ---------------------------------------------------------------------------


@router.post(
    "/profiles/{profile_id}/rechunk",
    response_model=None,
    responses={200: {"description": "SSE stream"}},
    summary="Rebuild a profile's chunks (SSE)",
)
async def rechunk(
    profile_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    sessionmaker_: Annotated[async_sessionmaker[AsyncSession], Depends(_sessionmaker_dep)],
    embedder: Annotated[Embedder, Depends(_embedder_dep)],
) -> EventSourceResponse:
    return EventSourceResponse(
        _sse_rechunk(
            sessionmaker=sessionmaker_,
            user_id=user_id,
            profile_id=profile_id,
            embedder=embedder,
        )
    )


async def _sse_rechunk(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    profile_id: int,
    embedder: Embedder,
) -> AsyncIterator[dict[str, str]]:
    """Yield the rechunk SSE sequence.

    Mirrors parse SSE: ownership check before `started` (so 404 / wrong-
    user appears as `error → done` without a misleading `started`).
    Embed failures are surfaced as `error → done` (unlike the parse
    tail where the parse already succeeded and we keep going).
    """
    job_id = generate_uuid7()
    try:
        async with sessionmaker() as session:
            await get_profile(session, user_id=user_id, profile_id=profile_id)
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return

    yield _sse("started", {"job_id": job_id, "resource_id": profile_id})

    try:
        result = await rebuild_for_profile(
            sessionmaker,
            user_id=user_id,
            profile_id=profile_id,
            embedder=embedder,
            trace_id=job_id,
        )
    except JobCopilotError as exc:
        yield _sse("error", {"code": exc.code, "detail": exc.detail})
        yield _sse("done", {"ok": False})
        return
    except Exception as exc:
        log.warning(
            "profile_rechunk.failed", profile_id=profile_id, error=str(exc), trace_id=job_id
        )
        yield _sse("error", {"code": "EMBED_FAILED", "detail": str(exc)})
        yield _sse("done", {"ok": False})
        return

    yield _sse("chunking_embedding", _rebuild_payload(result))
    yield _sse("done", {"ok": True})


# ---------------------------------------------------------------------------
# GET /v1/profiles/{id}/chunks
# ---------------------------------------------------------------------------


@router.get(
    "/profiles/{profile_id}/chunks",
    response_model=ProfileChunksResponse,
    summary="List a profile's chunks (no embedding payload)",
)
async def chunks(
    profile_id: int,
    user_id: Annotated[int, Depends(current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileChunksResponse:
    profile = await get_profile(session, user_id=user_id, profile_id=profile_id)
    rows = list(
        (
            await session.execute(
                sa.select(ProfileChunk)
                .where(ProfileChunk.profile_id == profile.id)
                .order_by(ProfileChunk.id)
            )
        )
        .scalars()
        .all()
    )
    return ProfileChunksResponse(
        data=[_chunk_item(r) for r in rows],
        total=len(rows),
    )


def _chunk_item(row: ProfileChunk) -> ProfileChunkItem:
    return ProfileChunkItem(
        id=row.id,
        granularity=row.granularity,
        source_table=row.source_table,
        source_id=row.source_id,
        content=row.content,
        embed_model=row.embed_model,
        embed_version=row.embed_version,
        metadata=dict(row.chunk_metadata),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
