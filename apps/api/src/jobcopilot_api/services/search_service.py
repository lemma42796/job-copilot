"""Hybrid search(沿用 v1 RRF + tsvector + pgvector;M1)。

职责:
- 节点局部 hybrid search(出题前剪枝,> 30 chunks 用)
- global_hybrid_search:跨用户全部笔记的 hybrid search,
  暴露给 AnswerJudge 的 lookup_in_notes_global tool(5-AGENT §4.7)

底层 tokenize 走 services/tokenize.py(已落地,沿用 v1)。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def hybrid_search_in_node(
    session: AsyncSession,
    folder_path: list[str],
    heading_path: list[str] | None,
    query: str,
    top_k: int = 30,
) -> list:
    raise NotImplementedError("M1")


async def global_hybrid_search(
    session: AsyncSession,
    query: str,
    top_k: int = 3,
) -> list:
    """AnswerJudge tool 入口(M2)。"""
    raise NotImplementedError("M2")
