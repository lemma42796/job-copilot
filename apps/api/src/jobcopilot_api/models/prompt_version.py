"""Prompt 版本管理。DATA_MODEL §3.17。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class PromptVersion(Base, IDMixin):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("agent_name", "version", name="uq_pv_agent_version"),)

    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default="[]",
    )

    eval_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # FK to eval_runs added in S6 when that table ships.
    eval_run_id: Mapped[int | None] = mapped_column(BigInteger)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
