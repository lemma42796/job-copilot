"""User 表(P0)。

多用户线上服务的身份主体。v1 的 users 表已在 alembic 0016 被 DROP,
本表由 0025 重新建立,列集合按当前需求收敛:认证只需要 email +
password_hash,其余是展示与配额挂载点。

password_hash 格式:`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`,
由 `services/auth_service.py` 生成与校验,不引入额外依赖。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class User(Base, IDMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    locale: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="zh-CN"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=func.cast("{}", postgresql.JSONB),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
