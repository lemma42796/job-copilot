"""Jd ORM — DATA_MODEL §5.8. 累积型 JD 库(M2.5)。

类比笔记:用户陆续上传,长期留存。上传即解析(jd_parser),parsed_payload
JSONB 持久化复用,后续一键分析免重 LLM。M2.5 只接文本粘贴;
历史 DB enum 里可能仍有 image_upload 值,但不再作为产品入口。

source ENUM `note_source` 历史共享:'text_paste'(jds) +
'local_md' / 'web_editor' (notes),另有不再作为产品入口的历史值。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, Numeric, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models._enums import NOTE_SOURCE_VALUES
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Jd(Base, IDMixin, TimestampMixin):
    __tablename__ = "jds"

    source: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *NOTE_SOURCE_VALUES, name="note_source", create_type=False
        ),
        nullable=False,
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
