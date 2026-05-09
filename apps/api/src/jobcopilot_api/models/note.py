"""Note ORM — DATA_MODEL §5.1.

一行 = 一篇 markdown 笔记(本地目录直读 / web 编辑器)。
chunker 从 content_md 取,不依赖磁盘文件。
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models._enums import NOTE_SOURCE_VALUES
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin


class Note(Base, IDMixin, TimestampMixin):
    __tablename__ = "notes"

    folder_path: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text()), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_md: Mapped[str] = mapped_column(Text(), nullable=False)

    source: Mapped[str] = mapped_column(
        postgresql.ENUM(
            *NOTE_SOURCE_VALUES, name="note_source", create_type=False
        ),
        nullable=False,
    )
