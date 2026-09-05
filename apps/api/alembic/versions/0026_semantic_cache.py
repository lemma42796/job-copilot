"""P6: llm_response_cache 加 user_id + 语义近似命中所需的向量列

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-05

精确命中靠 `cache_key`(0025 之后 key 里已经带了 user_id)。这里加的是
**近似命中**所需的两列:

- `user_id` —— 近似命中必须按用户隔离。`request` 里存的是用户原始输入
  (笔记片段、query),跨用户复用等于把 A 的笔记内容返回给 B。
- `request_embedding` —— 请求文本的向量,用 pgvector 余弦距离找近邻。

成本审计不变:近似命中仍然写 `llm_calls`(cost 记 0、cached=true),
`llm_response_cache` 里的原始 response 保留 tokens,想还原"本该花多少"
仍然可以从这里重建。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = 1024


def upgrade() -> None:
    op.add_column(
        "llm_response_cache", sa.Column("user_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "llm_response_cache", sa.Column("semantic_text", sa.Text(), nullable=True)
    )
    op.add_column(
        "llm_response_cache",
        sa.Column("request_embedding", Vector(EMBED_DIM), nullable=True),
    )
    op.create_index(
        "ix_llm_response_cache_user_feature",
        "llm_response_cache",
        ["user_id", "feature", "model"],
    )
    # 近邻检索按余弦距离。列可空,只有算过向量的行进索引。
    op.execute(
        """
        CREATE INDEX ix_llm_response_cache_embedding
        ON llm_response_cache
        USING hnsw (request_embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_response_cache_embedding")
    op.drop_index(
        "ix_llm_response_cache_user_feature", table_name="llm_response_cache"
    )
    op.drop_column("llm_response_cache", "request_embedding")
    op.drop_column("llm_response_cache", "semantic_text")
    op.drop_column("llm_response_cache", "user_id")
