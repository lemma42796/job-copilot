"""JD REST 端点(M2.5 / P0 / P3)。

router 自身 prefix `/jds` / `/jd-analyses`;main.py include 时挂 `/api`,
实际路径为 `/api/jds*` 和 `/api/jd-analyses*`。

P3:`POST /jd-analyses` 从"当场跑分析并流式返回"改成 **202 + job_id**。
旧实现把分析协程挂在 API 进程里,用一个进程内的订阅队列做断线重连 ——
多副本部署下事件只在跑任务的那个副本上,别的副本订阅不到。现在事件落
`job_events` 表,任何副本都能读。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.infra.auth import CurrentUserId
from jobcopilot_api.infra.db import get_session, get_sessionmaker
from jobcopilot_api.models.job import KIND_JD_ANALYSIS
from jobcopilot_api.schemas.jd import (
    JdAnalysisCreateIn,
    JdAnalysisListOut,
    JdAnalysisOut,
    JdCreateIn,
    JdListOut,
    JdOut,
    JdPatchIn,
)
from jobcopilot_api.schemas.job import JobAcceptedOut
from jobcopilot_api.services import jd_service, job_service

router = APIRouter(tags=["jd"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/jds",
    response_model=JdOut,
    status_code=201,
    summary="上传文本 JD 并立即解析入库",
)
async def create_jd(
    payload: JdCreateIn, session: SessionDep, user_id: CurrentUserId
) -> JdOut:
    jd = await jd_service.upload_jd(session, payload, user_id=user_id)
    await session.commit()
    return jd


@router.get("/jds", response_model=JdListOut, summary="列 JD 库")
async def list_jds(
    session: SessionDep,
    user_id: CurrentUserId,
    title: str | None = None,
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> JdListOut:
    return await jd_service.list_jds(
        session,
        cursor=cursor,
        limit=limit,
        title=title,
        user_id=user_id,
    )


@router.get("/jds/{jd_id}", response_model=JdOut, summary="JD 详情")
async def get_jd(
    jd_id: int, session: SessionDep, user_id: CurrentUserId
) -> JdOut:
    return await jd_service.get_jd(session, jd_id, user_id=user_id)


@router.patch("/jds/{jd_id}", response_model=JdOut, summary="修改 JD title")
async def patch_jd(
    jd_id: int,
    payload: JdPatchIn,
    session: SessionDep,
    user_id: CurrentUserId,
) -> JdOut:
    jd = await jd_service.patch_jd(session, jd_id, payload, user_id=user_id)
    await session.commit()
    return jd


@router.delete("/jds/{jd_id}", status_code=204, summary="软删 JD")
async def delete_jd(
    jd_id: int, session: SessionDep, user_id: CurrentUserId
) -> None:
    await jd_service.delete_jd(session, jd_id, user_id=user_id)
    await session.commit()


@router.post(
    "/jd-analyses",
    summary="JD 一键分析(异步,返回 job_id)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_jd_analysis(
    payload: JdAnalysisCreateIn, user_id: CurrentUserId
) -> JobAcceptedOut:
    async with get_sessionmaker()() as session:
        await job_service.assert_queue_has_room(session)
        analysis = await jd_service.create_analysis_placeholder(
            session, payload, user_id=user_id
        )
        analysis_id = analysis.id
        jd_count = analysis.jd_count
        job = await job_service.enqueue(
            session,
            user_id=user_id,
            kind=KIND_JD_ANALYSIS,
            payload={"analysis_id": analysis_id, "jd_count": jd_count},
            resource_kind="jd_analysis",
            resource_id=analysis_id,
            dedupe_key=f"jd_analysis:{analysis_id}",
        )
        await session.commit()
        accepted = JobAcceptedOut(
            job_id=job.id,
            status=job.status,
            kind=job.kind,
            resource_kind="jd_analysis",
            resource_id=analysis_id,
        )
    await job_service.publish(accepted.job_id)
    return accepted


@router.get(
    "/jd-analyses",
    response_model=JdAnalysisListOut,
    summary="JD 分析报告列表",
)
async def list_jd_analyses(
    session: SessionDep,
    user_id: CurrentUserId,
    cursor: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> JdAnalysisListOut:
    return await jd_service.list_analyses(
        session, cursor=cursor, limit=limit, user_id=user_id
    )


@router.get(
    "/jd-analyses/{analysis_id}",
    response_model=JdAnalysisOut,
    summary="JD 分析报告详情",
)
async def get_jd_analysis(
    analysis_id: int, session: SessionDep, user_id: CurrentUserId
) -> JdAnalysisOut:
    return await jd_service.get_analysis(
        session, analysis_id, user_id=user_id
    )
