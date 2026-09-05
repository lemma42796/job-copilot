"""SessionAnswer ORM — DATA_MODEL §5.5.

一行 = session 里一题 + 用户答 + 三层评分 + Judge audit。session 出题时
立即落 N 行(user_answer NULL),用户每答 UPDATE 一次,Judge 跑完再 UPDATE 一次。
没有 deleted_at:跟 quiz_sessions 走 CASCADE 硬删。

三层 evidence 是 JSONB(schema 见 DATA_MODEL §6.2 / §6.3 / §6.4);total_score
由 Python SSoT 算(0.5×Coverage + 0.4×Fidelity + 0.1×Depth)。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class SessionAnswer(Base, IDMixin):
    __tablename__ = "session_answers"

    # P0 数据隔离:所有业务表按 user_id 归属,service 层每条查询都必须带上它。
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    user_answer: Mapped[str | None] = mapped_column(Text())
    answer_turns: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    answer_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    remediation_state: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    coverage_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    coverage_evidence: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    fidelity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    fidelity_evidence: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    depth_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    depth_evidence: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    coach_message: Mapped[str | None] = mapped_column(Text())

    judge_model: Mapped[str | None] = mapped_column(String(50))
    judge_prompt_version: Mapped[str | None] = mapped_column(String(20))
    judge_tokens_in: Mapped[int | None] = mapped_column(Integer)
    judge_tokens_out: Mapped[int | None] = mapped_column(Integer)
    judge_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
