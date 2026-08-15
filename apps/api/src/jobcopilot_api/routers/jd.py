"""JD REST + SSE 端点(M2.5,OpenAPI / Pydantic schemas)。

router 自身 prefix `/jds` / `/jd-analyses`;main.py include 时挂 `/api`,
实际路径为 `/api/jds*` 和 `/api/jd-analyses*`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from jobcopilot_api.infra.db import get_session, get_sessionmaker
from jobcopilot_api.schemas.jd import (
    JdAnalysisCreateIn,
    JdAnalysisListOut,
    JdAnalysisOut,
    JdCreateIn,
    JdListOut,
    JdOut,
    JdPatchIn,
)
from jobcopilot_api.services import jd_service

router = APIRouter(tags=["jd"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/jds",
    response_model=JdOut,
    status_code=201,
    summary="上传文本 JD 并立即解析入库",
)
async def create_jd(payload: JdCreateIn, session: SessionDep) -> JdOut:
    jd = await jd_service.upload_jd(session, payload)
    await session.commit()
    return jd


@router.get(
    "/jds",
    response_model=JdListOut,
    summary="列 JD 库",
)
async def list_jds(
    session: SessionDep,
    title: str | None = None,
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> JdListOut:
    return await jd_service.list_jds(
        session,
        cursor=cursor,
        limit=limit,
        title=title,
    )


@router.get(
    "/jds/{jd_id}",
    response_model=JdOut,
    summary="JD 详情",
)
async def get_jd(jd_id: int, session: SessionDep) -> JdOut:
    return await jd_service.get_jd(session, jd_id)


@router.patch(
    "/jds/{jd_id}",
    response_model=JdOut,
    summary="修改 JD title",
)
async def patch_jd(
    jd_id: int,
    payload: JdPatchIn,
    session: SessionDep,
) -> JdOut:
    jd = await jd_service.patch_jd(session, jd_id, payload)
    await session.commit()
    return jd


@router.delete(
    "/jds/{jd_id}",
    status_code=204,
    summary="软删 JD",
)
async def delete_jd(jd_id: int, session: SessionDep) -> None:
    await jd_service.delete_jd(session, jd_id)
    await session.commit()


@router.post(
    "/jd-analyses",
    summary="JD 一键分析(SSE)",
)
async def create_jd_analysis(payload: JdAnalysisCreateIn) -> EventSourceResponse:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        analysis = await jd_service.create_analysis_placeholder(session, payload)
        analysis_id = analysis.id
        jd_count = analysis.jd_count
        await session.commit()

    jd_service.launch_analysis(
        sessionmaker,
        analysis_id=analysis_id,
        jd_count=jd_count,
    )

    async def _events() -> AsyncIterator[dict[str, Any]]:
        async for event in jd_service.observe_analysis_sse(
            sessionmaker,
            analysis_id=analysis_id,
        ):
            yield event

    return EventSourceResponse(_events())


@router.get(
    "/jd-analyses",
    response_model=JdAnalysisListOut,
    summary="JD 分析报告列表",
)
async def list_jd_analyses(
    session: SessionDep,
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> JdAnalysisListOut:
    return await jd_service.list_analyses(session, cursor=cursor, limit=limit)


@router.get(
    "/jd-analyses/{analysis_id}",
    response_model=JdAnalysisOut,
    summary="JD 分析报告详情",
)
async def get_jd_analysis(
    analysis_id: int,
    session: SessionDep,
) -> JdAnalysisOut:
    return await jd_service.get_analysis(session, analysis_id)


@router.get(
    "/jd-analyses/{analysis_id}/events",
    summary="恢复观察 JD 分析进度(SSE)",
)
async def observe_jd_analysis(analysis_id: int) -> EventSourceResponse:
    sessionmaker = get_sessionmaker()
    return EventSourceResponse(
        jd_service.observe_analysis_sse(
            sessionmaker,
            analysis_id=analysis_id,
        )
    )
