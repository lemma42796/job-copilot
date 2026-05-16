"""Question ORM — DATA_MODEL §5.3.

出题来源是 query 不是节点(M2 起聊天框 query 替代节点点击);
originated_query / originated_mode 留作 audit / 复用判断 / 评测 query 多样性。

evidence_chunk_ids 是 SSoT 数组,由 service 从 QuizGenerator 的 [N] 引用
和采分点 evidence 派生;Judge 同一份顺序对照。
reference_answer_chunk_ids ⊆ evidence_chunk_ids。
scoring_points 见 §6.1 schema(用于 Coverage 算分)。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Integer, Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models._enums import (
    QUESTION_TYPE_VALUES,
    QUIZ_SESSION_MODE_VALUES,
)
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Question(Base, IDMixin, TimestampMixin):
    __tablename__ = "questions"

    originated_query: Mapped[str] = mapped_column(Text(), nullable=False)
    originated_mode: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *QUIZ_SESSION_MODE_VALUES,
            name="quiz_session_mode",
            create_type=False,
        ),
        nullable=False,
        server_default="topic",
    )

    type: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *QUESTION_TYPE_VALUES, name="question_type", create_type=False
        ),
        nullable=False,
    )
    prompt: Mapped[str] = mapped_column(Text(), nullable=False)

    evidence_chunk_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger()), nullable=False
    )

    reference_answer: Mapped[str] = mapped_column(Text(), nullable=False)
    reference_answer_chunk_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger()), nullable=False
    )
    scoring_points: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    gen_model: Mapped[str | None] = mapped_column(String(50))
    gen_prompt_version: Mapped[str | None] = mapped_column(String(20))
    gen_tokens_in: Mapped[int | None] = mapped_column(Integer)
    gen_tokens_out: Mapped[int | None] = mapped_column(Integer)
    gen_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
