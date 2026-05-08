"""Resume ORM — DATA_MODEL §5.9. 简历(M3)。

简历短(< 5KB),诊断时直接整 content_md 喂 LLM,**不进 hybrid search 索引**。
parsed_chunks JSONB 存段落切片(position / type / content),resume_advisor
suggestion 锚点引用 position 字段。用户可有多份简历(不同岗位定制版本对比)。

source 字段是 VARCHAR 不是 ENUM(取值 'markdown_paste' / 'pdf_upload' 由
应用层 Pydantic 校验)— 跟 jds 走 note_source ENUM 不同,因 resumes 入口
跟笔记 / JD 不共享。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Resume(Base, IDMixin, TimestampMixin):
    __tablename__ = "resumes"

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    content_md: Mapped[str] = mapped_column(Text(), nullable=False)

    parsed_chunks: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    parse_model: Mapped[str | None] = mapped_column(String(50))
    parse_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
