"""P3/P4 job worker —— 把长任务从在线请求里搬到独立进程。

一条 job 的生命周期:

1. 在线接口 `job_service.enqueue` 建行(status=queued)+ commit + `publish`。
2. worker 收到 RabbitMQ 投递的 job_id；未确认的投递由后台补发到 RabbitMQ。
   只有一次能把 queued 改为 running，避免重复调用 LLM 和扣费。
   执行结束后才 ACK。已领取的任务中断后，由超期回收处理。
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
from jobcopilot_api.infra.queue import DeadLetter, RabbitMQJobQueue, get_queue
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


async def _consume_rabbitmq(queue: RabbitMQJobQueue, slots: asyncio.Semaphore) -> None:
    async def handle(job_id: int, redelivered: bool) -> None:
        async with slots:
            job = await job_service.claim(job_id)
            if job is not None:
                try:
                    async with asyncio.timeout(job_service.remaining_seconds(job)):
                        await run_job(job)
                except TimeoutError:
                    await job_service.finish(job_id=job_id, status=job_service.DEADLINE_EXCEEDED,
                                             error_code="deadline_exceeded")
            status = await job_service.job_status(job_id)
            if status in (job_service.FAILED, job_service.DEADLINE_EXCEEDED) or status is None:
                raise DeadLetter(status or "unknown_job")
            if job is None and status == job_service.RUNNING and redelivered:
                raise DeadLetter("redelivered_while_running")

    while True:
        try:
            await queue.consume(handle)
        except Exception as exc:
            logger.exception("rabbitmq_consumer_error", error=str(exc))
        await asyncio.sleep(settings.job_poll_interval_s)


async def _relay_pending(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if await job_service.publish_pending():
                continue
        except Exception:
            logger.exception("job_publication_retry_failed")
        await asyncio.sleep(settings.job_poll_interval_s)


async def run_forever(stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    slots = asyncio.Semaphore(settings.job_worker_concurrency)
    async with asyncio.TaskGroup() as tasks:
        consumer = tasks.create_task(_consume_rabbitmq(get_queue(), slots))
        relay = tasks.create_task(_relay_pending(stop))
        await stop.wait()
        consumer.cancel()
        relay.cancel()


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
