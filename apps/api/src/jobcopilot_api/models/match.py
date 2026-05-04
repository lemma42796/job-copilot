"""Match ORM. DATA_MODEL §3.9 / migration 0011.

DB-only FKs on `user_id` (CASCADE) / `jd_id` (CASCADE) / `profile_id`
(CASCADE) live in 0011; per ADR-0005 D1 we don't navigate FKs from
Python.

Schema 偏离 §3.9 (见 0011 docstring):
- `score` nullable: pending / failed 时 NULL,只有 status='scored' 时由
  service 层保证非空(check 约束在 DB 层做范围兜底).
- 新增 `status` enum (pending / scored / failed): 永久约束 #4 SSE 起手要
  resource_id,phase 1 INSERT 一条 pending 行,与 jds.status 模式对齐.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin

MATCH_STATUS_ENUM = postgresql.ENUM(
    "pending",
    "scored",
    "failed",
    name="match_status",
    create_type=False,
)


class Match(Base, IDMixin, TimestampMixin):
    __tablename__ = "matches"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jd_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(MATCH_STATUS_ENUM, nullable=False, server_default="pending")
    score: Mapped[int | None] = mapped_column(SmallInteger)

    matched_skills: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    missing_skills: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    advantage_summary: Mapped[str | None] = mapped_column(Text)
    gap_summary: Mapped[str | None] = mapped_column(Text)
    suggestions: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    model: Mapped[str | None] = mapped_column(String(50))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # created_at / updated_at / deleted_at from TimestampMixin.
