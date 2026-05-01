"""LLM 调用日志(成本归因)。DATA_MODEL §3.16。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class LlmCall(Base, IDMixin):
    __tablename__ = "llm_calls"

    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL", name="fk_lc_user_id"),
    )

    feature: Mapped[str] = mapped_column(String(50), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    thinking_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)

    cost_cny: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50))

    trace_id: Mapped[str | None] = mapped_column(String(100))
    related_entity: Mapped[str | None] = mapped_column(String(50))
    related_id: Mapped[int | None] = mapped_column(BigInteger)

    prompt_version_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "prompt_versions.id",
            ondelete="SET NULL",
            name="fk_lc_prompt_version_id",
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
