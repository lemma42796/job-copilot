"""RabbitMQ 只传 job_id，任务参数和状态保存在 PostgreSQL。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import aio_pika
import structlog
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustQueue
from aio_pika.connection import make_url

from jobcopilot_api.settings import settings

logger = structlog.get_logger(__name__)

BROKER_TIMEOUT_S = 5.0


class DeadLetter(Exception):
    """不能安全重试的任务。"""


class RabbitMQJobQueue:
    def __init__(self, url: str, name: str) -> None:
        self._url = url
        self._name = name
        self._lock = asyncio.Lock()
        self._connection: aio_pika.RobustConnection | None = None
        self._queue: AbstractRobustQueue | None = None

    async def _connect(self) -> AbstractRobustQueue:
        async with self._lock:
            if self._queue is not None:
                return self._queue

            connection = aio_pika.RobustConnection(make_url(self._url))
            try:
                await connection.connect(timeout=BROKER_TIMEOUT_S)
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                await channel.declare_queue(f"{self._name}.dead", durable=True)
                queue = await channel.declare_queue(self._name, durable=True)
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
            self._queue = queue
            return queue

    async def publish(self, job_id: int) -> None:
        async with asyncio.timeout(BROKER_TIMEOUT_S):
            queue = await self._connect()
            await queue.channel.default_exchange.publish(
                aio_pika.Message(
                    body=str(job_id).encode("ascii"),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=queue.name,
                mandatory=True,
            )

    async def consume(self, handle: Callable[[int, bool], Awaitable[None]]) -> None:
        async with asyncio.timeout(BROKER_TIMEOUT_S):
            queue = await self._connect()
            await queue.channel.set_qos(prefetch_count=settings.job_worker_concurrency)

        async with queue.iterator() as messages, asyncio.TaskGroup() as tasks:
            async for message in messages:
                tasks.create_task(self._process(message, handle))

    async def _process(
        self, message: AbstractIncomingMessage, handle: Callable[[int, bool], Awaitable[None]]
    ) -> None:
        try:
            async with message.process(requeue=True):
                try:
                    job_id = int(message.body)
                    if job_id <= 0 or job_id > 2**63 - 1:
                        raise ValueError
                except ValueError:
                    reason = "invalid_job_id"
                else:
                    for attempt in range(4):
                        try:
                            await handle(job_id, message.redelivered or attempt > 0)
                            return
                        except DeadLetter as exc:
                            reason = str(exc)
                            break
                        except Exception as exc:
                            logger.exception("job_delivery_failed", job_id=job_id, attempt=attempt + 1)
                            reason = type(exc).__name__
                            if attempt < 3:
                                await asyncio.sleep(2**attempt)

                # 先确认死信持久投递，再由上下文确认原消息。
                queue = await self._connect()
                await queue.channel.default_exchange.publish(
                    aio_pika.Message(body=message.body,
                                     headers={"failure": reason},
                                     delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                    routing_key=f"{self._name}.dead", mandatory=True,
                    timeout=BROKER_TIMEOUT_S,
                )
                logger.warning("job_dead_lettered", reason=reason)
        except Exception:
            logger.exception("queue_message_processing_error", delivery_tag=message.delivery_tag)

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None
                self._queue = None


_queue: RabbitMQJobQueue | None = None


def get_queue() -> RabbitMQJobQueue:
    global _queue
    url = settings.rabbitmq_url.strip()
    if _queue is None:
        _queue = RabbitMQJobQueue(url, settings.job_queue_name)
    return _queue


async def close_queue() -> None:
    global _queue
    if _queue is not None:
        await _queue.close()
        _queue = None
