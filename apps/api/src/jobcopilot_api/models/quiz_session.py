"""QuizSession ORM — DATA_MODEL §5.4.

一行 = 用户点"开始答题"产生的 session。三层分数 + total_score 都存(audit
需要;权重 SSoT 在 services/answer_service,不让 Judge 算)。abandoned_at
作字段(非状态)— 用户中途退出时同时写 status='abandoned' + abandoned_at。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models._enums import QUIZ_SESSION_STATUS_VALUES
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class QuizSession(Base, IDMixin, TimestampMixin):
    __tablename__ = "quiz_sessions"

    node_folder_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    node_heading_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
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
