"""NoteChunk ORM — DATA_MODEL §5.2.

heading-aware chunker 输出。folder_path / heading_path 反规范化(避免每次
join notes)。content_tsv 是 GENERATED STORED 列(DDL 0016 写源端表达式
`to_tsvector('simple', public.char_ngrams(content))`),ORM 不读不写。
embedding 由 embed worker 异步算,nullable;hybrid search 端 WHERE
embedding IS NOT NULL 过滤未算完的 chunk。

ADR-0005 D1:不写 relationship();note_id FK 在 DDL 层声明
ON DELETE CASCADE,ORM 只声明类型。
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class NoteChunk(Base, IDMixin):
    __tablename__ = "note_chunks"

    note_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    folder_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    heading_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    heading_level: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text(), nullable=False)

    # content_tsv 是 GENERATED STORED 列 — DDL 0016 维护,ORM 不暴露(读 / 写
    # 都不需要走 ORM,SQL 层 WHERE content_tsv @@ to_tsquery(...) 就行)。

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embed_model: Mapped[str | None] = mapped_column(String(50))
    embed_version: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
