"""Embedder 批量调用(M1)。

config(5-AGENT_DESIGN §2.1):
- model: text-embedding-v4
- thinking: N/A(非聊天模型)
- 不走 prompt_versions(无 prompt)

入参是 chunk 内容列表,返回 1024 维 vector list,顺序保持。
失败重试由 infra/embedder.py 兜底。
"""

from __future__ import annotations


async def embed_batch(texts: list[str]) -> list[list[float]]:
    raise NotImplementedError("M1")
