"""LLM response cache(S21 子任务 4-B)。

`cache_key` 由 `llm.cache_key.compute_cache_key` 算出(sha256 hex),把
`(model, system, augmented_user, response_format, thinking_mode,
prompt_version_id)` 锁死;任一改动 → 新 key,旧 cache 自然失效,无需手动
版本号或 TTL。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class LlmResponseCache(Base, IDMixin):
    __tablename__ = "llm_response_cache"

    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    feature: Mapped[str] = mapped_column(String(50), nullable=False)

    # FK to prompt_versions.id (ON DELETE SET NULL) declared in migration 0015.
    prompt_version_id: Mapped[int | None] = mapped_column(BigInteger)

    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
