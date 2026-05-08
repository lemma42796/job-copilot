"""Embed worker — 后台轮询 embedding=NULL 的 note_chunks 批量算(M1)。

数据流(2-TECH_DESIGN §5.5):
1. 笔记入库时 chunk_service 只切 chunks + 落 content / content_tsv,embedding 留 NULL
2. 本 worker 周期性扫 note_chunks WHERE embedding IS NULL ORDER BY id LIMIT BATCH_SIZE
3. 调 agents/embedder/agent.embed_batch(content_list) 拿 1024 维 vector list
4. UPDATE note_chunks SET embedding = ... WHERE id = ANY(:ids)(同事务)

启动 / 关停由 main.py lifespan 钩子负责(2-TECH §4.3)。worker 不直接走 router。

骨架阶段(M0):函数 stub。M1 落地时填具体 SQL + embedder 调用。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

BATCH_SIZE = 64
POLL_INTERVAL_SECONDS = 5.0


async def process_batch() -> int:
    """处理一批 embedding=NULL 的 chunks,返回处理数。0 表示当前队列已空。"""
    raise NotImplementedError("M1")


async def run_forever(stop_event: asyncio.Event) -> None:
    """主循环 — 直到 stop_event 被 set。

    错误恢复:单批次失败 logger.exception + 退避一轮 POLL_INTERVAL,
    不让一次失败把 worker 整体打挂(笔记入库链路对 worker 是 fire-and-forget)。
    """
    logger.info("embed_worker started")
    try:
        while not stop_event.is_set():
            try:
                processed = await process_batch()
            except NotImplementedError:
                # 骨架阶段;M1 移除此分支
                processed = 0
            except Exception:  # noqa: BLE001
                logger.exception("embed_worker batch failed")
                processed = 0

            # 队列空 → 退避;有处理 → 紧接下一批,不退避
            if processed == 0:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=POLL_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
    finally:
        logger.info("embed_worker stopped")
