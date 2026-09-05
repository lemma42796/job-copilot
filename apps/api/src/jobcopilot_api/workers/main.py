"""P4 worker 进程入口。

跟 API 进程完全分离:API 容器只处理在线请求(202 建 job + SSE 只读订阅),
所有会调用 LLM 的长任务都在这个进程里跑。这样在线接口的 p99 不再被
一次 8 轮 tool-calling 的判分拖住,worker 也能独立按队列深度横向扩副本。

进程内跑三个协程:

- `job_worker.run_forever` —— 消费 jobs 队列(Redis Streams 或 DB 轮询)
- `embed_worker.run_forever` —— 补 note_chunks 的 embedding
- `job_worker.reap_forever` —— 把超期 job 判死

启动:`python -m jobcopilot_api.workers.main`
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from jobcopilot_api.infra.logging import setup_logging
from jobcopilot_api.workers import embed_worker, job_worker

logger = structlog.get_logger(__name__)


async def _amain() -> None:
    setup_logging()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - 非 POSIX
            pass

    logger.info("worker_process_starting")
    tasks = [
        asyncio.create_task(job_worker.run_forever(stop), name="job_worker"),
        asyncio.create_task(embed_worker.run_forever(stop), name="embed_worker"),
        asyncio.create_task(job_worker.reap_forever(stop), name="job_reaper"),
    ]
    await stop.wait()
    logger.info("worker_process_stopping")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    from jobcopilot_api.infra.queue import get_queue

    await get_queue().close()
    logger.info("worker_process_stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
