"""ResumeVersion ORM. DATA_MODEL §3.11 / migration 0012.

每次成功生成或(M3)用户编辑保存,产生一条 version。MVP 阶段 generate 成
功后由 service 层插入 `version_number=1, edit_type='generated'`;regenerate /
edit / patch 留 M3。

§3.11 仅有 `created_at`(无 `updated_at` / `deleted_at`),所以不套
TimestampMixin —— 自己声明 `created_at` 一列。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class ResumeVersion(Base, IDMixin):
    __tablename__ = "resume_versions"

    resume_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_file_id: Mapped[int | None] = mapped_column(BigInteger)

    edit_type: Mapped[str | None] = mapped_column(String(20))
    edit_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
