"""Embed worker — 后台轮询 embedding=NULL 的 note_chunks 批量算(M1)。

数据流(docs/TECH_DESIGN.md):
1. 笔记入库时 chunk_service 只切 chunks + 落 content / content_tsv,embedding 留 NULL
2. 本 worker 周期性扫 note_chunks WHERE embedding IS NULL ORDER BY id LIMIT N
3. 调 agents/embedder.embed_batch(content_list) 拿 1024 维 vector list
4. UPDATE note_chunks SET embedding / embed_model / embed_version 同事务

P4 之后本 worker 不再由 API 进程的 lifespan 启动,而是跟 job_worker 一起跑在
独立的 worker 容器里(`workers/main.py`),API 容器只处理在线请求。

多副本安全:领取用 `FOR UPDATE SKIP LOCKED`,两个副本不会抢到同一批 chunk;
被锁住的行会被另一个副本跳过而不是排队等待。

计费归属:embedding 也要按用户扣费,所以 `embed_batch` 必须拿到 chunk 的
`user_id`。一轮里按 user_id 分组,每组独立成批。

错误恢复:单批失败 logger.exception + 退避一轮 POLL_INTERVAL,不让一次失败把
worker 整体打挂(笔记入库链路对 worker 是 fire-and-forget)。空 dashscope key 时
get_embedder 抛 ValueError,被同一个 except 吞,worker 静默退避等待 key 配置。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import select

from jobcopilot_api.agents.embedder.agent import EMBED_VERSION, embed_batch
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.settings import settings

logger = logging.getLogger(__name__)

# 跟百炼 EMBED_BATCH_LIMIT(10)对齐 — 单 batch 上限。
BATCH_SIZE = 10
POLL_INTERVAL_SECONDS = 5.0


def _batch_concurrency() -> int:
    return max(1, settings.embed_worker_batch_concurrency)


async def process_batch() -> int:
    """处理一轮 chunks,返回处理数。0 表示当前队列已空。

    一轮最多领 `BATCH_SIZE * embed_worker_batch_concurrency` 条,按 user_id
    切成多个 ≤BATCH_SIZE 的批并发调用 embedder;并发上限由 llm/admission 的
    信号量再兜一层,不会因为这里放大而打穿上游配额。
    """
    sm = get_sessionmaker()
    async with sm() as session:
        stmt = (
            select(NoteChunk)
            .where(NoteChunk.embedding.is_(None))
            .order_by(NoteChunk.id)
            .limit(BATCH_SIZE * _batch_concurrency())
            # 多副本:锁住本轮领到的行,别的副本跳过而不是阻塞。
            .with_for_update(skip_locked=True)
        )
        chunks = list((await session.execute(stmt)).scalars().all())
        if not chunks:
            return 0

        by_user: dict[int, list[NoteChunk]] = defaultdict(list)
        for chunk in chunks:
            by_user[chunk.user_id].append(chunk)

        groups: list[tuple[int, list[NoteChunk]]] = []
        for user_id, owned in by_user.items():
            for start in range(0, len(owned), BATCH_SIZE):
                groups.append((user_id, owned[start : start + BATCH_SIZE]))

        results = await asyncio.gather(
            *(
                embed_batch([c.content for c in group], user_id=user_id)
                for user_id, group in groups
            ),
            return_exceptions=True,
        )

        processed = 0
        failures: list[BaseException] = []
        for (_user_id, group), result in zip(groups, results, strict=True):
            if isinstance(result, BaseException):
                # 这一批留着下轮重试;别的批照常落库。
                failures.append(result)
                continue
            for chunk, vec in zip(group, result.vectors, strict=True):
                chunk.embedding = vec
                chunk.embed_model = result.model
                chunk.embed_version = EMBED_VERSION
            processed += len(group)

        await session.commit()
        for exc in failures:
            logger.warning("embed_worker batch failed: %s", exc)
        return processed


async def run_forever(stop_event: asyncio.Event) -> None:
    """主循环 — 直到 stop_event 被 set。"""
    logger.info("embed_worker started")
    try:
        while not stop_event.is_set():
            try:
                processed = await process_batch()
            except Exception:
                logger.exception("embed_worker batch failed")
                processed = 0

            # 队列空 → 退避;有处理 → 紧接下一轮,不退避
            if processed == 0:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=POLL_INTERVAL_SECONDS
                    )
                except TimeoutError:
                    pass
    finally:
        logger.info("embed_worker stopped")
