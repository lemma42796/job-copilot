"""JD ORM. DATA_MODEL §3.2 / migration 0003.

DB-only FKs on `user_id` (CASCADE) and `raw_file_id` (SET NULL) live in
migration 0003; we do not navigate `User.jds` or `Jd.raw_file` from
Python, so following ADR-0005 D1 we declare neither here.

The `search_tsv` GENERATED column is omitted from the ORM — business
code never reads it; full-text search runs as a `text()` predicate
against the column directly via the GIN index.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin

JD_SOURCE_ENUM = postgresql.ENUM(
    "text_paste",
    "pdf_upload",
    "image_upload",
    "extension_paste",
    name="jd_source",
    create_type=False,
)
JD_STATUS_ENUM = postgresql.ENUM(
    "parsing",
    "parsed",
    "parse_failed",
    name="jd_status",
    create_type=False,
)


class Jd(Base, IDMixin, TimestampMixin):
    __tablename__ = "jds"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    source: Mapped[str] = mapped_column(JD_SOURCE_ENUM, nullable=False)
    status: Mapped[str] = mapped_column(JD_STATUS_ENUM, nullable=False, server_default="parsing")

    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_file_id: Mapped[int | None] = mapped_column(BigInteger)

    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(100))

    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(10), server_default="CNY")
    salary_period: Mapped[str | None] = mapped_column(String(10), server_default="monthly")
    salary_months: Mapped[int | None] = mapped_column(SmallInteger)

    job_level: Mapped[str | None] = mapped_column(String(50))
    years_required: Mapped[int | None] = mapped_column(Integer)
    education: Mapped[str | None] = mapped_column(String(50))

    hard_skills: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    soft_skills: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    bonus_skills: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    responsibilities: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    description: Mapped[str | None] = mapped_column(Text)

    parse_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    parse_model: Mapped[str | None] = mapped_column(String(50))
    parse_tokens: Mapped[int | None] = mapped_column(Integer)
    parse_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    # `search_tsv tsvector GENERATED ALWAYS AS (...) STORED` is in DDL
    # but intentionally absent from the ORM (see module docstring).

    # `created_at` / `updated_at` / `deleted_at` from TimestampMixin.
