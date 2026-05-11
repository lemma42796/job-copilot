"""QuizSession ORM — DATA_MODEL §5.4.

一行 = 用户聊天框输 query 后产生的 session(M2 起聊天框 query 替代节点点击)。

字段分组:
- 出题入口三件:query / mode / jd_ids
- M3 SR 审计字段(M2 一并预建):trigger / gap_folder_path / gap_heading_path
- retrieval pipeline 审计快照:expanded_queries / retrieved_chunk_ids
- 三层评分汇总:total / coverage / fidelity / depth(权重 SSoT 在
  services/answer_service,不让 Judge 算)
- abandoned_at 作字段(非状态)— 用户中途退出 / 0 命中守门时同时写
  status='abandoned' + abandoned_at
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models._enums import (
    QUIZ_SESSION_MODE_VALUES,
    QUIZ_SESSION_STATUS_VALUES,
)
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class QuizSession(Base, IDMixin, TimestampMixin):
    __tablename__ = "quiz_sessions"

    query: Mapped[str] = mapped_column(Text(), nullable=False)
    mode: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *QUIZ_SESSION_MODE_VALUES,
            name="quiz_session_mode",
            create_type=False,
        ),
        nullable=False,
        server_default="topic",
    )
    jd_ids: Mapped[list[int] | None] = mapped_column(
        postgresql.ARRAY(BigInteger())
    )

    trigger: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="manual",
    )
    gap_folder_path: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(Text())
    )
    gap_heading_path: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(Text())
    )

    expanded_queries: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(Text())
    )
    retrieved_chunk_ids: Mapped[list[int] | None] = mapped_column(
        postgresql.ARRAY(BigInteger())
    )

    status: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *QUIZ_SESSION_STATUS_VALUES,
            name="quiz_session_status",
            create_type=False,
        ),
        nullable=False,
        server_default="in_progress",
    )

    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    coverage_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    fidelity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    depth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    recall_md_path: Mapped[str | None] = mapped_column(Text())

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
