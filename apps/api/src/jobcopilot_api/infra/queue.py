"""P4 任务队列 —— Redis Streams(consumer group + XACK + XAUTOCLAIM)。

设计要点:

- **消费组**:所有 worker 副本加入同一个 group(`settings.job_consumer_group`),
  每条 stream 消息只投递给组内一个消费者。
- **XACK**:handler 跑完(不论成功还是终态失败)才 ack;进程崩溃时消息留在
  pending 列表。
- **XAUTOCLAIM**:定期把闲置超过 `settings.job_claim_min_idle_ms` 的 pending
  消息转到当前消费者名下,实现崩溃接管。
- **幂等**:stream 消息只携带 `job_id`。真正的去重发生在
  `job_service.claim()` 的条件写(`WHERE status='queued'`),所以重复投递 /
  重复接管都不会重复调用 LLM 或重复扣费。
- **降级**:`settings.redis_url` 为空时 `publish` 是空操作,worker 走
  `job_service.claim_next_queued()` 的 `FOR UPDATE SKIP LOCKED` 轮询。
  单机开发不需要起 Redis。
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from os import getpid
from typing import Any

import structlog

from jobcopilot_api.settings import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class QueueMessage:
    """一条待消费的队列消息。

    `ack_id` 为 None 表示这条消息来自 DB 轮询兜底路径,无需 ack。
    """

    job_id: int
    ack_id: str | None = None


class JobQueue:
    """队列抽象。两个实现:Redis Streams 与 DB 轮询兜底。"""

    async def publish(self, job_id: int) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def consume(self) -> AsyncIterator[QueueMessage]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]

    async def ack(self, message: QueueMessage) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - 接口
        return None


class NullQueue(JobQueue):
    """Redis 未配置时的兜底:不投递,worker 直接轮询 jobs 表。"""

    async def publish(self, job_id: int) -> None:
        return None

    async def consume(self) -> AsyncIterator[QueueMessage]:
        # 交给 worker 的 DB 轮询分支,这里不产出任何消息。
        return
        yield  # type: ignore[unreachable]

    async def ack(self, message: QueueMessage) -> None:
        return None


class RedisStreamQueue(JobQueue):
    def __init__(self, url: str) -> None:
        self._url = url
        self._redis: Any | None = None
        self._group_ready = False
        self._consumer = f"{socket.gethostname()}-{getpid()}"

    async def _client(self) -> Any:
        if self._redis is None:
            from redis.asyncio import Redis  # 延迟导入:API 进程不装也能跑

            self._redis = Redis.from_url(self._url, decode_responses=True)
        if not self._group_ready:
            await self._ensure_group(self._redis)
        return self._redis

    async def _ensure_group(self, redis: Any) -> None:
        from redis.exceptions import ResponseError

        try:
            await redis.xgroup_create(
                name=settings.job_stream_key,
                groupname=settings.job_consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def publish(self, job_id: int) -> None:
        redis = await self._client()
        await redis.xadd(settings.job_stream_key, {"job_id": str(job_id)})

    async def consume(self) -> AsyncIterator[QueueMessage]:
        """先接管闲置 pending,再读新消息。每次调用产出 0..N 条。"""
        redis = await self._client()
        block_ms = max(1, int(settings.job_poll_interval_s * 1000))

        # 1) 崩溃接管:把别的消费者名下超时未 ack 的消息转给自己。
        claimed = await redis.xautoclaim(
            name=settings.job_stream_key,
            groupname=settings.job_consumer_group,
            consumername=self._consumer,
            min_idle_time=settings.job_claim_min_idle_ms,
            start_id="0-0",
            count=settings.job_worker_concurrency,
        )
        for entry in _entries_from_autoclaim(claimed):
            message = _to_message(entry)
            if message is None:
                await redis.xack(
                    settings.job_stream_key, settings.job_consumer_group, entry[0]
                )
                continue
            yield message

        # 2) 新消息。
        response = await redis.xreadgroup(
            groupname=settings.job_consumer_group,
            consumername=self._consumer,
            streams={settings.job_stream_key: ">"},
            count=settings.job_worker_concurrency,
            block=block_ms,
        )
        for _stream, entries in response or []:
            for entry in entries:
                message = _to_message(entry)
                if message is None:
                    await redis.xack(
                        settings.job_stream_key,
                        settings.job_consumer_group,
                        entry[0],
                    )
                    continue
                yield message

    async def ack(self, message: QueueMessage) -> None:
        if message.ack_id is None:
            return
        redis = await self._client()
        await redis.xack(
            settings.job_stream_key, settings.job_consumer_group, message.ack_id
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._group_ready = False


def _entries_from_autoclaim(result: Any) -> list[Any]:
    """XAUTOCLAIM 返回 (next_id, entries) 或 (next_id, entries, deleted)。"""
    if not result or len(result) < 2:
        return []
    return list(result[1] or [])


def _to_message(entry: Any) -> QueueMessage | None:
    entry_id, fields = entry
    raw = (fields or {}).get("job_id")
    try:
        job_id = int(raw)
    except (TypeError, ValueError):
        logger.warning("queue_message_invalid", entry_id=entry_id, fields=fields)
        return None
    return QueueMessage(job_id=job_id, ack_id=entry_id)


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    """进程内单例。`redis_url` 为空返回 NullQueue。"""
    global _queue
    if _queue is None:
        url = (settings.redis_url or "").strip()
        _queue = RedisStreamQueue(url) if url else NullQueue()
        logger.info("job_queue_initialized", backend=type(_queue).__name__)
    return _queue


def reset_queue() -> None:
    """测试 / 重连用:丢弃单例。"""
    global _queue
    _queue = None


def queue_enabled() -> bool:
    return bool((settings.redis_url or "").strip())
