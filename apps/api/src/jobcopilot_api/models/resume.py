"""Resume ORM. DATA_MODEL §3.10 / migration 0012.

DB-only FKs on `user_id` / `jd_id` / `profile_id` (CASCADE)、`match_id`
(SET NULL)、`pdf_file_id` (SET NULL) live in 0012;per ADR-0005 D1 we
don't navigate FKs from Python.

Status 与 §3.10 完全对齐(`generating` 替代 match 的 `pending`)。
`generating` 走永久约束 #4:phase-1 INSERT 一行,SSE `started` 事件即可
带 resource_id。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin

RESUME_STATUS_ENUM = postgresql.ENUM(
    "generating",
    "review_failed",
    "ready",
    "failed",
    name="resume_status",
    create_type=False,
)


class Resume(Base, IDMixin, TimestampMixin):
    __tablename__ = "resumes"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jd_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    match_id: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[str] = mapped_column(
        RESUME_STATUS_ENUM, nullable=False, server_default="generating"
    )

    title: Mapped[str | None] = mapped_column(String(200))
    markdown: Mapped[str | None] = mapped_column(Text)
    pdf_file_id: Mapped[int | None] = mapped_column(BigInteger)
    template: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="awesome-cv-zh"
    )

    generation_model: Mapped[str | None] = mapped_column(String(50))
    review_model: Mapped[str | None] = mapped_column(String(50))
    revisions: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    review_passed: Mapped[bool | None] = mapped_column(Boolean)
    review_findings: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # created_at / updated_at / deleted_at from TimestampMixin.
