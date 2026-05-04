"""Profile + child ORMs. DATA_MODEL §3.3-§3.7 / migrations 0004 + 0005 + 0009.

Per ADR-0005 D1 we never navigate FKs from Python (e.g. no
`Profile.experiences` `relationship()`) — child queries always go
through `select(ProfileExperience).where(profile_id == ...)`. DB-only
FKs (CASCADE on parent delete) live in migration 0004.

`ProfileChunk` (migration 0005) maps `content` + `embedding` (1024-dim
pgvector) + `metadata` JSONB. The Python attribute is `chunk_metadata`
because the bare name `metadata` collides with `DeclarativeBase.metadata`
(class-level `MetaData` registry). The DB column name stays `metadata`.
The DDL-only `content_tsv` (Postgres-computed tsvector for FTS) is
intentionally absent from the ORM, mirroring the `Jd.search_tsv` pattern.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin

PROFILE_SOURCE_ENUM = postgresql.ENUM(
    "pdf_upload",
    "text_paste",
    "manual",
    name="profile_source",
    create_type=False,
)
PROFILE_STATUS_ENUM = postgresql.ENUM(
    "parsing",
    "parsed",
    "parse_failed",
    name="profile_status",
    create_type=False,
)
SKILL_LEVEL_ENUM = postgresql.ENUM(
    "beginner",
    "intermediate",
    "advanced",
    "expert",
    name="skill_level",
    create_type=False,
)
CHUNK_GRANULARITY_ENUM = postgresql.ENUM(
    "experience",
    "project",
    "skill",
    "summary",
    "education",
    name="chunk_granularity",
    create_type=False,
)


class Profile(Base, IDMixin, TimestampMixin):
    __tablename__ = "profiles"
    # Per-user uniqueness is enforced by the partial unique INDEX
    # `uq_profiles_user_id WHERE deleted_at IS NULL` in migration 0009 —
    # a plain UniqueConstraint here would misrepresent the soft-delete-
    # aware semantics.

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    source: Mapped[str] = mapped_column(PROFILE_SOURCE_ENUM, nullable=False)
    status: Mapped[str] = mapped_column(
        PROFILE_STATUS_ENUM, nullable=False, server_default="parsing"
    )

    full_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)

    target_titles: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    target_locations: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    target_salary_min: Mapped[int | None] = mapped_column(Integer)
    target_salary_max: Mapped[int | None] = mapped_column(Integer)

    raw_file_id: Mapped[int | None] = mapped_column(BigInteger)
    raw_text: Mapped[str | None] = mapped_column(Text)

    parse_model: Mapped[str | None] = mapped_column(String(50))
    parse_tokens: Mapped[int | None] = mapped_column(Integer)
    parse_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))


class ProfileExperience(Base, IDMixin):
    __tablename__ = "profile_experiences"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR start_date <= end_date",
            name="ck_pe_dates",
        ),
    )

    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    company: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    description: Mapped[str] = mapped_column(Text, nullable=False)
    bullets: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tech_stack: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    achievements: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfileProject(Base, IDMixin):
    __tablename__ = "profile_projects"

    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    experience_id: Mapped[int | None] = mapped_column(BigInteger)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    bullets: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tech_stack: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    achievements: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    repo_url: Mapped[str | None] = mapped_column(String(500))
    demo_url: Mapped[str | None] = mapped_column(String(500))

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfileSkill(Base, IDMixin):
    __tablename__ = "profile_skills"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_ps_profile_name"),)

    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_raw: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(50))
    level: Mapped[str | None] = mapped_column(SKILL_LEVEL_ENUM)
    years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    evidence_project_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger),
        nullable=False,
        server_default=text("'{}'::bigint[]"),
    )
    evidence_experience_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger),
        nullable=False,
        server_default=text("'{}'::bigint[]"),
    )

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfileEducation(Base, IDMixin):
    __tablename__ = "profile_educations"

    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    school: Mapped[str] = mapped_column(String(200), nullable=False)
    degree: Mapped[str | None] = mapped_column(String(50))
    major: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    gpa: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    honors: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProfileChunk(Base, IDMixin):
    __tablename__ = "profile_chunks"

    profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    granularity: Mapped[str] = mapped_column(CHUNK_GRANULARITY_ENUM, nullable=False)
    source_table: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # `content_tsv` (DDL-computed tsvector) is intentionally omitted; FTS queries
    # use raw `to_tsquery` against the column without ORM round-trip.

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    # DB column is `metadata`; Python attr renamed to dodge `DeclarativeBase.metadata`.
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        postgresql.JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    embed_model: Mapped[str | None] = mapped_column(String(50))
    embed_version: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
