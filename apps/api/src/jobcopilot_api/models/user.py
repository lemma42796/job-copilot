"""User ORM. DATA_MODEL §3.1 / migration 0002.

S2-E deliberately skipped this model and used a DB-only FK on
`llm_calls.user_id` (ADR-0004 D1). S3 needs to navigate
`User.files`, so the model lands here. Other relationships (jds,
profiles, ...) ship with their own slices and stay out of this file
to keep the mapper graph minimal (ADR-0005 D1).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin
from jobcopilot_api.models.file import File


class User(Base, IDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    locale: Mapped[str] = mapped_column(String(10), nullable=False, server_default="zh-CN")
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    files: Mapped[list[File]] = relationship()
