"""Jd ORM — DATA_MODEL §5.7. 累积型 JD 库(M2.5)。

类比笔记:用户陆续上传,长期留存。上传即解析(jd_parser),parsed_payload
JSONB 持久化复用,后续一键分析免重 LLM。截图场景:source='image_upload',
raw_text 存 Qwen 多模态 OCR 后的文本(原图不存)。

source ENUM `note_source` 共享:'text_paste' / 'image_upload' (jds) +
'zip_upload' / 'web_editor' / 'yuque' (notes)— 5 值同一 ENUM。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, Numeric, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Jd(Base, IDMixin, TimestampMixin):
    __tablename__ = "jds"

    source: Mapped[str] = mapped_column(
        postgresql.ENUM(name="note_source", create_type=False), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text(), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))

    parsed_payload: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False
    )

    parse_model: Mapped[str | None] = mapped_column(String(50))
    parse_prompt_version: Mapped[str | None] = mapped_column(String(20))
    parse_tokens_in: Mapped[int | None] = mapped_column(Integer)
    parse_tokens_out: Mapped[int | None] = mapped_column(Integer)
    parse_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
