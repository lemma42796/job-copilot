"""KnowledgeGap ORM — DATA_MODEL §5.6.

一行 = 被跟踪的知识点节点(folder_path + heading_path)。session 评分后 upsert,
SR 算法用 prev_interval_days + next_review_at 排队。

(folder_path, heading_path) 是业务主键 — 即使 chunk_id 漂移,只要节点路径
还在,跟踪连续。空 heading_path = '{}' 合法(节点选到 folder 级)。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, Text, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class KnowledgeGap(Base, IDMixin):
    __tablename__ = "knowledge_gaps"

    # P0 数据隔离:所有业务表按 user_id 归属,service 层每条查询都必须带上它。
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    folder_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    heading_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prev_interval_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    next_review_at: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
