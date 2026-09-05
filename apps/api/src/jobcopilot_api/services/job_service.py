"""异步任务编排(P3)。

职责边界:

- **在线侧**(routers):`enqueue` 写一行 `jobs` 并推队列,立刻返回;
  `observe_job_sse` 只读 `job_events`,不碰 LLM。
- **worker 侧**:`claim` 条件写领取任务,`append_event` 逐条落事件,
  `finish` 写终态。

事件顺序由 `job_events.seq` 保证,`(job_id, seq)` 唯一。订阅方带上已收到
的 `after_seq` 就能从断点续读 —— 断线重连不丢事件,也不需要在内存里缓存
进度(那正是旧 `jd_service` 内存订阅队列的问题)。

幂等:领取是 `UPDATE jobs SET status='running' WHERE id=:id AND status IN
('queued',)` 的条件写,只有一个消费者能改到行。重复投递的消息在第二次
领取时拿不到行,直接 ack 丢弃,不会重复调用 LLM 或重复扣费。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobcopilot_api.errors import JobCopilotError, NotFoundError
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.job import (
    DEADLINE_EXCEEDED,
    FAILED,
    INSUFFICIENT_BALANCE,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TERMINAL_JOB_STATUSES,
    Job,
    JobEvent,
)
from jobcopilot_api.settings import settings

log = structlog.get_logger(__name__)


class QueueOverloadedError(JobCopilotError):
    """P7:待执行队列超过水位,拒绝新任务而不是让队列无限增长。

    响应带 `Retry-After: settings.queue_retry_after_seconds`。
    """

    status_code = 503
    code = "queue_overloaded"
    title = "任务队列已满,请稍后重试"

    def __init__(self, detail: str = "", **kwargs: Any) -> None:
        super().__init__(detail, **kwargs)
        self.headers["Retry-After"] = str(settings.queue_retry_after_seconds)


@dataclass(frozen=True)
class JobSnapshot:
    id: int
    user_id: int
    kind: str
    status: str
    resource_kind: str | None
    resource_id: int | None
    result: dict[str, Any] | None
    error_code: str | None
    error_detail: str | None
    last_seq: int
    created_at: datetime
    finished_at: datetime | None


# ---------- 在线侧 ----------


async def assert_queue_has_room(session: AsyncSession) -> None:
    """P7:队列水位闸门。超阈值直接 503,不入队。"""
    pending = int(
        (
            await session.execute(
                sa.select(sa.func.count(Job.id)).where(
                    Job.status.in_((QUEUED, RUNNING))
                )
            )
        ).scalar_one()
    )
    if pending >= settings.queue_high_watermark:
        raise QueueOverloadedError(
            f"待执行任务 {pending} 已达水位 {settings.queue_high_watermark}"
        )


async def enqueue(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    payload: dict[str, Any],
    resource_kind: str | None = None,
    resource_id: int | None = None,
    dedupe_key: str | None = None,
    deadline_s: int | None = None,
) -> Job:
    """建 job 行。调用方 commit 之后再 `publish`。

    `dedupe_key` 非空时,同一键上已有非终态 job 就直接复用那一行 —— 用户
    连点两次提交不会跑两遍 LLM。
    """
    if dedupe_key:
        existing = (
            await session.execute(
                sa.select(Job)
                .where(Job.user_id == user_id)
                .where(Job.dedupe_key == dedupe_key)
                .where(Job.status.notin_(tuple(TERMINAL_JOB_STATUSES)))
                .order_by(Job.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    seconds = deadline_s or settings.job_default_deadline_s
    job = Job(
        user_id=user_id,
        kind=kind,
        status=QUEUED,
        payload=payload,
        resource_kind=resource_kind,
        resource_id=resource_id,
        dedupe_key=dedupe_key,
        deadline_at=datetime.now(UTC) + timedelta(seconds=seconds),
    )
    session.add(job)
    await session.flush()
    return job


async def publish(job_id: int) -> None:
    """把 job_id 推进队列。Redis 未配置时是空操作 —— worker 轮询兜底。"""
    from jobcopilot_api.infra.queue import get_queue

    await get_queue().publish(job_id)


async def get_job(
    session: AsyncSession, job_id: int, *, user_id: int
) -> JobSnapshot:
    job = (
        await session.execute(
            sa.select(Job).where(Job.id == job_id).where(Job.user_id == user_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError(f"job {job_id} 不存在")
    return _to_snapshot(job)


async def read_events(
    session: AsyncSession,
    *,
    job_id: int,
    user_id: int,
    after_seq: int = 0,
    limit: int = 200,
) -> list[JobEvent]:
    return list(
        (
            await session.execute(
                sa.select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .where(JobEvent.user_id == user_id)
                .where(JobEvent.seq > after_seq)
                .order_by(JobEvent.seq)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def observe_job_sse(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    user_id: int,
    after_seq: int = 0,
):
    """只读的进度订阅。

    每轮只做一次索引查询(`job_events` 按 `(job_id, seq)` 有唯一索引),
    拿完就把 session 还回池 —— 单个在线请求持有数据库 session 的时间等于
    一次查询,而不是整个任务时长。
    """
    # 先确认归属,避免任何人拿别人的 job_id 订阅事件。
    async with sessionmaker() as session:
        snapshot = await get_job(session, job_id, user_id=user_id)

    yield _sse(
        "started",
        {
            "job_id": job_id,
            "status": snapshot.status,
            "kind": snapshot.kind,
            "resource_id": snapshot.resource_id,
            "resource_kind": snapshot.resource_kind,
        },
    )

    seq = after_seq
    deadline = asyncio.get_running_loop().time() + settings.job_sse_idle_timeout_s
    while True:
        async with sessionmaker() as session:
            events = await read_events(
                session, job_id=job_id, user_id=user_id, after_seq=seq
            )
            status = (
                await session.execute(
                    sa.select(Job.status)
                    .where(Job.id == job_id)
                    .where(Job.user_id == user_id)
                )
            ).scalar_one()

        for event in events:
            seq = event.seq
            yield _sse(event.event, {**dict(event.data or {}), "seq": event.seq})

        if status in TERMINAL_JOB_STATUSES and not events:
            # 终态且事件已读完 —— handler 自己写过 done 就不再补一条。
            yield _sse(
                "job_finished", {"job_id": job_id, "status": status, "seq": seq}
            )
            return

        if asyncio.get_running_loop().time() > deadline:
            yield _sse(
                "timeout",
                {"job_id": job_id, "status": status, "seq": seq},
            )
            return

        if not events:
            await asyncio.sleep(settings.job_event_poll_interval_s)


# ---------- worker 侧 ----------


async def claim(job_id: int) -> Job | None:
    """条件写领取。返回 None 表示这条 job 已被别人领走或已终态。"""
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        job = (
            await session.execute(
                sa.update(Job)
                .where(Job.id == job_id)
                .where(Job.status == QUEUED)
                .values(
                    status=RUNNING,
                    started_at=now,
                    heartbeat_at=now,
                    attempt_count=Job.attempt_count + 1,
                    updated_at=now,
                )
                .returning(Job)
            )
        ).scalar_one_or_none()
        await session.commit()
        return job


async def claim_next_queued() -> Job | None:
    """无 Redis 时的轮询领取。`FOR UPDATE SKIP LOCKED` 让多副本不抢同一行。"""
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        candidate = (
            await session.execute(
                sa.select(Job.id)
                .where(Job.status == QUEUED)
                .order_by(Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return None
        job = (
            await session.execute(
                sa.update(Job)
                .where(Job.id == candidate)
                .where(Job.status == QUEUED)
                .values(
                    status=RUNNING,
                    started_at=now,
                    heartbeat_at=now,
                    attempt_count=Job.attempt_count + 1,
                    updated_at=now,
                )
                .returning(Job)
            )
        ).scalar_one_or_none()
        await session.commit()
        return job


async def append_event(
    *,
    job_id: int,
    user_id: int,
    event: str,
    data: dict[str, Any],
) -> int:
    """写一条事件并返回它的 seq。seq 由 jobs.last_seq 原子自增分配。"""
    async with get_sessionmaker()() as session:
        seq = (
            await session.execute(
                sa.update(Job)
                .where(Job.id == job_id)
                .values(
                    last_seq=Job.last_seq + 1,
                    heartbeat_at=datetime.now(UTC),
                )
                .returning(Job.last_seq)
            )
        ).scalar_one()
        session.add(
            JobEvent(
                job_id=job_id,
                user_id=user_id,
                seq=seq,
                event=event,
                data=data,
            )
        )
        await session.commit()
        return int(seq)


async def finish(
    *,
    job_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            sa.update(Job)
            .where(Job.id == job_id)
            .values(
                status=status,
                result=result,
                error_code=error_code,
                error_detail=error_detail,
                finished_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def heartbeat(job_id: int) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            sa.update(Job)
            .where(Job.id == job_id)
            .values(heartbeat_at=datetime.now(UTC))
        )
        await session.commit()


async def is_past_deadline(job: Job) -> bool:
    """P7:任务 deadline。超期直接判死并释放 worker。"""
    if job.deadline_at is None:
        return False
    return datetime.now(UTC) > job.deadline_at


async def reap_expired_jobs() -> int:
    """把超期仍在 running / queued 的 job 判死。已消耗的部分照常已扣费。"""
    async with get_sessionmaker()() as session:
        result = await session.execute(
            sa.update(Job)
            .where(Job.status.in_((QUEUED, RUNNING)))
            .where(Job.deadline_at.is_not(None))
            .where(Job.deadline_at < datetime.now(UTC))
            .values(
                status=DEADLINE_EXCEEDED,
                error_code="deadline_exceeded",
                error_detail="任务超过 deadline,已判死并释放 worker",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


# ---------- helpers ----------

# 事件流里出现这些 code 时,job 终态是"余额耗尽中止"而不是"失败"。
_INSUFFICIENT_BALANCE_CODE = "insufficient_balance"


def terminal_status_for(events: list[tuple[str, dict[str, Any]]]) -> tuple[str, str | None, str | None]:
    """从 handler 产出的事件推导 job 终态。

    余额耗尽必须与执行失败区分开:前端据此提示充值而不是报错。
    """
    ok = True
    error_code: str | None = None
    error_detail: str | None = None
    for name, data in events:
        if name == "error":
            error_code = str(data.get("code") or "internal_error")
            error_detail = str(data.get("detail") or "")
        elif name == "done":
            ok = bool(data.get("ok"))
    if ok and error_code is None:
        return SUCCEEDED, None, None
    if error_code == _INSUFFICIENT_BALANCE_CODE:
        return INSUFFICIENT_BALANCE, error_code, error_detail
    return FAILED, error_code or "internal_error", error_detail


def _to_snapshot(job: Job) -> JobSnapshot:
    return JobSnapshot(
        id=job.id,
        user_id=job.user_id,
        kind=job.kind,
        status=job.status,
        resource_kind=job.resource_kind,
        resource_id=job.resource_id,
        result=job.result,
        error_code=job.error_code,
        error_detail=job.error_detail,
        last_seq=job.last_seq,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(data, ensure_ascii=False, default=str),
    }
