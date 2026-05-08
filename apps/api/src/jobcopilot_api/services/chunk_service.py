"""Heading-aware markdown chunker(M1,从 v1 改造)。

职责:
- 把 note 的 content_md 切成 note_chunks(按 heading 树切,heading_path
  反映层级);chunk 落 content + content_tsv,embedding 留 NULL 给 worker 补
- 重新切片(笔记内容更新时):删旧 chunks → 切新 chunks(同事务)

不做:LLM 调用;embedding(由 workers/embed_worker 异步补)。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def rechunk_note(session: AsyncSession, note_id: int) -> int:
    """返回切片数量。"""
    raise NotImplementedError("M1")


async def get_chunks_for_node(
    session: AsyncSession,
    folder_path: list[str],
    heading_path: list[str] | None,
    limit: int = 30,
) -> list:
    """节点 prefix 命中 chunks(下限 5 / 上限 30,见 5-AGENT §3.1)。"""
    raise NotImplementedError("M1")
