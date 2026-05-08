"""JdAnalysis ORM — DATA_MODEL §5.8. 一键分析快照(M2.5)。

每次"一键分析"产出一行,jd_ids 数组锁定本次范围。**快照不更新** — 报告
生成后不可改;用户后续删除某条 JD 不影响历史报告(jd_ids 里 id 可能 dangling,
前端按需过滤)。status 状态机:in_progress → done / failed。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class JdAnalysis(Base, IDMixin):
    __tablename__ = "jd_analyses"

    jd_ids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(postgresql.BIGINT()), nullable=False
    )
    jd_count: Mapped[int] = mapped_column(Integer, nullable=False)
    filter_description: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="in_progress"
    )

    aggregated_requirements: Mapped[list[dict[str, Any]] | None] = mapped_column(
        postgresql.JSONB
    )
    learning_path_md: Mapped[str | None] = mapped_column(Text())

    total_tokens_in: Mapped[int | None] = mapped_column(Integer)
    total_tokens_out: Mapped[int | None] = mapped_column(Integer)
    total_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    cache_hit_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text())
