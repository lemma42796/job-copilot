"""Question ORM — DATA_MODEL §5.3.

source_chunk_ids 是 SSoT 数组,出题 prompt 里 [N] 编号顺序跟数组顺序一致;
Judge 同一份顺序对照。reference_chunk_ids ⊆ source_chunk_ids(LLM 强约束)。
reference_points 见 §6.1 schema(用于 Coverage 算分)。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Integer, Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Question(Base, IDMixin, TimestampMixin):
    __tablename__ = "questions"

    node_folder_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    node_heading_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    type: Mapped[str] = mapped_column(
        postgresql.ENUM(name="question_type", create_type=False), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text(), nullable=False)

    source_chunk_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger()), nullable=False
    )

    reference_answer: Mapped[str] = mapped_column(Text(), nullable=False)
    reference_chunk_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger()), nullable=False
    )
    reference_points: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    gen_model: Mapped[str | None] = mapped_column(String(50))
    gen_prompt_version: Mapped[str | None] = mapped_column(String(20))
    gen_tokens_in: Mapped[int | None] = mapped_column(Integer)
    gen_tokens_out: Mapped[int | None] = mapped_column(Integer)
    gen_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
