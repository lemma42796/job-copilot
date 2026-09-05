"""P3/P4 job worker —— 把长任务从在线请求里搬到独立进程。

一条 job 的生命周期:

1. 在线接口 `job_service.enqueue` 建行(status=queued)+ commit + `publish`。
2. worker 收到 job_id(Redis Streams)或轮询到它(无 Redis 时),
   走 `job_service.claim` 的条件写把 queued→running。**条件写是幂等闸门**:
   重复投递 / XAUTOCLAIM 接管 / worker 重启重放,只有一次能把状态从 queued
   改走,所以 LLM 不会被调两次,余额也不会被扣两次。
3. handler 消费对应 service 的 async generator,每 yield 一条事件就
   `job_service.append_event` 落库(seq 由 jobs.last_seq 原子自增分配)。
   在线侧的订阅接口读这张表,不需要和 worker 共享内存。
4. 事件流结束后由 `job_service.terminal_status_for` 推终态。余额耗尽是
   独立终态 `insufficient_balance`,与执行失败区分开 —— 已经产生的结果
   保留在库里,不回滚。

handler 都是"消费已有 generator"而不是重写业务逻辑:generator 内部已经
按 P0 带上 user_id、按 P1 在每次上游调用前查余额。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import structlog

from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.infra.queue import QueueMessage, get_queue, queue_enabled
from jobcopilot_api.models.job import (
    KIND_ANSWER_TURN,
    KIND_JD_ANALYSIS,
    KIND_QUIZ_CREATE,
    KIND_SESSION_FINISH,
    KIND_SESSION_SUBMIT,
    Job,
)
from jobcopilot_api.schemas.quiz import AnswerTurnSubmitIn, QuizSessionCreateIn
from jobcopilot_api.services import (
    answer_service,
    interview_service,
    jd_service,
    job_service,
    quiz_service,
)
from jobcopilot_api.settings import settings

logger = structlog.get_logger(__name__)

EventStream = AsyncIterator[dict[str, Any]]
Handler = Callable[[Job], EventStream]


# ---------- handlers ----------


def _quiz_create(job: Job) -> EventStream:
    payload = dict(job.payload or {})
    session_id = payload.pop("session_id", None)
    return quiz_service.start_session_sse(
        get_sessionmaker(),
        QuizSessionCreateIn.model_validate(payload),
        user_id=job.user_id,
        session_id=session_id,
    )


def _answer_turn(job: Job) -> EventStream:
    payload = dict(job.payload or {})
    return interview_service.submit_answer_turn_sse(
        get_sessionmaker(),
        int(payload["session_id"]),
        int(payload["order_index"]),
        AnswerTurnSubmitIn.model_validate(payload["body"]),
        user_id=job.user_id,
    )


def _session_finish(job: Job) -> EventStream:
    payload = dict(job.payload or {})
    return interview_service.finish_session_sse(
        get_sessionmaker(),
        int(payload["session_id"]),
        user_id=job.user_id,
    )


def _session_submit(job: Job) -> EventStream:
    payload = dict(job.payload or {})
    return answer_service.submit_session_sse(
        get_sessionmaker(),
        int(payload["session_id"]),
        user_id=job.user_id,
    )


def _jd_analysis(job: Job) -> EventStream:
    payload = dict(job.payload or {})
    return jd_service.run_analysis_events(
        get_sessionmaker(),
        analysis_id=int(payload["analysis_id"]),
        jd_count=int(payload.get("jd_count") or 0),
        user_id=job.user_id,
    )


HANDLERS: dict[str, Handler] = {
    KIND_QUIZ_CREATE: _quiz_create,
    KIND_ANSWER_TURN: _answer_turn,
    KIND_SESSION_FINISH: _session_finish,
    KIND_SESSION_SUBMIT: _session_submit,
    KIND_JD_ANALYSIS: _jd_analysis,
}


# ---------- 执行 ----------


async def run_job(job: Job) -> None:
    """跑一条已经 claim 到手的 job,并把终态写回。"""
    handler = HANDLERS.get(job.kind)
    if handler is None:
        await job_service.finish(
            job_id=job.id,
            status=job_service.FAILED,
            error_code="unknown_job_kind",
            error_detail=f"未注册的 job kind: {job.kind}",
        )
        return

    log = logger.bind(job_id=job.id, kind=job.kind, user_id=job.user_id)
    seen: list[tuple[str, dict[str, Any]]] = []
    try:
        async for raw in handler(job):
            name = str(raw.get("event") or "message")
            data = _decode(raw.get("data"))
            seen.append((name, data))
            await job_service.append_event(
                job_id=job.id,
                user_id=job.user_id,
                event=name,
                data=data,
            )
            if await job_service.is_past_deadline(job):
                await job_service.append_event(
                    job_id=job.id,
                    user_id=job.user_id,
                    event="error",
                    data={
                        "code": "deadline_exceeded",
                        "detail": "任务超过 deadline,已中止;已产生的结果保留",
                    },
                )
                await job_service.finish(
                    job_id=job.id,
                    status=job_service.DEADLINE_EXCEEDED,
                    error_code="deadline_exceeded",
                )
                return
    except Exception as exc:  # noqa: BLE001 - worker 顶层兜底,不能让协程死掉
        log.exception("job_failed", error=str(exc))
        await job_service.append_event(
            job_id=job.id,
            user_id=job.user_id,
            event="error",
            data={"code": "internal_error", "detail": str(exc)},
        )
        await job_service.finish(
            job_id=job.id,
            status=job_service.FAILED,
            error_code="internal_error",
            error_detail=str(exc),
        )
        return

    status, error_code, error_detail = job_service.terminal_status_for(seen)
    await job_service.finish(
        job_id=job.id,
        status=status,
        result=_result_from(seen),
        error_code=error_code,
        error_detail=error_detail,
    )
    log.info("job_finished", status=status)


def _decode(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {"raw": data}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _result_from(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """把事件流里最后一条带资源标识的事件留给在线侧当结果摘要。"""
    for name, data in reversed(events):
        if name in {"session", "result", "analysis", "summary"}:
            return data
    return None


# ---------- 消费循环 ----------


async def _consume_once(semaphore: asyncio.Semaphore) -> bool:
    """取一批任务并跑。返回 True 表示这轮确实处理了东西。"""
    queue = get_queue()
    handled = False

    async def _process(message: QueueMessage) -> None:
        async with semaphore:
            job = await job_service.claim(message.job_id)
            if job is not None:
                await run_job(job)
            # claim 返回 None:别的副本已经领走或已终态,ack 掉即可。
            await queue.ack(message)

    if queue_enabled():
        tasks = [
            asyncio.create_task(_process(message))
            async for message in queue.consume()
        ]
        if tasks:
            handled = True
            await asyncio.gather(*tasks, return_exceptions=True)
        return handled

    # 无 Redis:直接从 jobs 表 FOR UPDATE SKIP LOCKED 领。
    job = await job_service.claim_next_queued()
    if job is None:
        return False
    async with semaphore:
        await run_job(job)
    return True


async def run_forever(stop: asyncio.Event | None = None) -> None:
    """worker 主循环。空转时按 `job_poll_interval_s` 退避。"""
    stop = stop or asyncio.Event()
    semaphore = asyncio.Semaphore(settings.job_worker_concurrency)
    logger.info(
        "job_worker_started",
        concurrency=settings.job_worker_concurrency,
        queue="redis" if queue_enabled() else "db-poll",
    )
    while not stop.is_set():
        try:
            handled = await _consume_once(semaphore)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 循环不能因为一次异常退出
            logger.exception("job_worker_loop_error", error=str(exc))
            handled = False
        if not handled:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.job_poll_interval_s
                )
            except TimeoutError:
                pass
    logger.info("job_worker_stopped")


async def reap_forever(stop: asyncio.Event | None = None) -> None:
    """P7:定期把超期 job 判死,防止 running 行永远挂着。"""
    stop = stop or asyncio.Event()
    interval = max(5.0, settings.job_poll_interval_s * 30)
    while not stop.is_set():
        try:
            reaped = await job_service.reap_expired_jobs()
            if reaped:
                logger.info("jobs_reaped", count=reaped)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job_reaper_error", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
