"""异步任务进度接口(P3)。

这两个端点是**只读**的:它们不调用 LLM、不写业务表,只查 `jobs` /
`job_events`。真正的执行在 worker 进程里。

- `GET /api/jobs/{id}` —— 一次性查终态,给不想开 SSE 的调用方(轮询、
  页面刷新后补状态)。
- `GET /api/jobs/{id}/events` —— SSE 订阅。带 `after_seq` 从断点续读,
  所以断线重连不丢事件。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from jobcopilot_api.infra.auth import CurrentUserId
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.schemas.job import JobEventListOut, JobEventOut, JobOut
from jobcopilot_api.services import job_service

router = APIRouter(tags=["jobs"], prefix="/jobs")


@router.get("/{job_id}", summary="查询异步任务状态")
async def get_job(job_id: int, user_id: CurrentUserId) -> JobOut:
    async with get_sessionmaker()() as session:
        snapshot = await job_service.get_job(session, job_id, user_id=user_id)
    return JobOut(
        id=snapshot.id,
        kind=snapshot.kind,
        status=snapshot.status,
        resource_kind=snapshot.resource_kind,
        resource_id=snapshot.resource_id,
        result=snapshot.result,
        error_code=snapshot.error_code,
        error_detail=snapshot.error_detail,
        last_seq=snapshot.last_seq,
        created_at=snapshot.created_at,
        finished_at=snapshot.finished_at,
    )


@router.get("/{job_id}/events", summary="拉取异步任务事件(非流式)")
async def list_job_events(
    job_id: int,
    user_id: CurrentUserId,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> JobEventListOut:
    async with get_sessionmaker()() as session:
        snapshot = await job_service.get_job(session, job_id, user_id=user_id)
        rows = await job_service.read_events(
            session,
            job_id=job_id,
            user_id=user_id,
            after_seq=after_seq,
            limit=limit,
        )
    return JobEventListOut(
        job_id=job_id,
        status=snapshot.status,
        events=[
            JobEventOut(
                seq=r.seq,
                event=r.event,
                data=r.data or {},
                created_at=r.created_at,
            )
            for r in rows
        ],
    )


@router.get("/{job_id}/stream", summary="订阅异步任务进度(SSE)")
async def stream_job_events(
    job_id: int,
    user_id: CurrentUserId,
    after_seq: int = Query(default=0, ge=0),
) -> EventSourceResponse:
    return EventSourceResponse(
        job_service.observe_job_sse(
            get_sessionmaker(),
            job_id=job_id,
            user_id=user_id,
            after_seq=after_seq,
        )
    )
