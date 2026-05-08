"""ResumeAnalysis ORM — DATA_MODEL §5.10. 简历诊断快照(M3)。

一行 = 一次"JD 报告 + 简历"的诊断快照。jd_analysis_id + resume_id 不要求 unique
(同一对组合可反复跑 — 用户改简历后重诊断)。anchored_count / unanchored_count
冗余字段方便 dashboard 列表展示。

两方锚点严格(DATA_MODEL §6.10):suggestions JSONB 里每条 (req_id +
resume_position) 双非空 = anchored;任一空 = unanchored。永不输出改写文案
(suggestion_topic 只描述"该补什么主题",不写具体文字)。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin


class ResumeAnalysis(Base, IDMixin):
    __tablename__ = "resume_analyses"

    jd_analysis_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resume_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="in_progress"
    )

    suggestions: Mapped[list[dict[str, Any]] | None] = mapped_column(postgresql.JSONB)
    anchored_count: Mapped[int | None] = mapped_column(Integer)
    unanchored_count: Mapped[int | None] = mapped_column(Integer)
    coverage_summary: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)

    llm_model: Mapped[str | None] = mapped_column(String(50))
    llm_prompt_version: Mapped[str | None] = mapped_column(String(20))
    total_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text())
