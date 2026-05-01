"""ORM declarative base + shared mixins.

Schema-level conventions live in `alembic/versions/` (DDL is authoritative).
The mixins here mirror DATA_MODEL §1.3 so that ORM reads/writes stay in sync.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root for all ORM models."""


class IDMixin:
    """`id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`."""

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )


class TimestampMixin:
    """`created_at` / `updated_at` / `deleted_at` per DATA_MODEL §1.3.

    `updated_at` is maintained by the `tg_<table>_set_updated_at` trigger
    installed in migrations; we still set a server_default for inserts that
    bypass the trigger (e.g. `COPY`).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
