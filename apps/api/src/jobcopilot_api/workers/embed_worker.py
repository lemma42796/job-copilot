"""Embed worker — 后台轮询 embedding=NULL 的 note_chunks 批量算(M1)。

数据流(docs/TECH_DESIGN.md):
1. 笔记入库时 chunk_service 只切 chunks + 落 content / content_tsv,embedding 留 NULL
2. 本 worker 周期性扫 note_chunks WHERE embedding IS NULL ORDER BY id LIMIT BATCH_SIZE
3. 调 agents/embedder.embed_batch(content_list) 拿 1024 维 vector list
4. UPDATE note_chunks SET embedding / embed_model / embed_version 同事务

启动 / 关停由 main.py lifespan 钩子负责(docs/TECH_DESIGN.md)。worker 不直接走 router。

错误恢复:单批失败 logger.exception + 退避一轮 POLL_INTERVAL,不让一次失败把
worker 整体打挂(笔记入库链路对 worker 是 fire-and-forget)。空 dashscope key 时
get_embedder 抛 ValueError,被同一个 except 吞,worker 静默退避等待 key 配置。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from jobcopilot_api.agents.embedder.agent import EMBED_VERSION, embed_batch
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.note_chunk import NoteChunk

logger = logging.getLogger(__name__)

# 跟百炼 EMBED_BATCH_LIMIT(10)对齐 — 单 batch 上限。需要更高吞吐就一轮多调
# 几次 batch,M1 简化为一轮一 batch(队列空才退避,所以连续清空很快)。
BATCH_SIZE = 10
POLL_INTERVAL_SECONDS = 5.0


async def process_batch() -> int:
    """处理一批 embedding=NULL 的 chunks,返回处理数。0 表示当前队列已空。

    embed 调用走 agents/embedder.embed_batch(已 langfuse instrument)。
    """
    sm = get_sessionmaker()
    async with sm() as session:
        stmt = (
            select(NoteChunk)
            .where(NoteChunk.embedding.is_(None))
            .order_by(NoteChunk.id)
            .limit(BATCH_SIZE)
        )
        chunks = list((await session.execute(stmt)).scalars().all())
        if not chunks:
            return 0

        texts = [c.content for c in chunks]
        result = await embed_batch(texts)

        for chunk, vec in zip(chunks, result.vectors, strict=True):
            chunk.embedding = vec
            chunk.embed_model = result.model
            chunk.embed_version = EMBED_VERSION

        await session.commit()
        return len(chunks)


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
            except Exception:
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
