"""共享 ENUM values。

SQLAlchemy 的 `postgresql.ENUM(name="...", create_type=False)` 必须显式列出
values,否则 SELECT 反映射时会抛 LookupError(只 INSERT 不报错)。

值列表必须跟 `alembic/versions/0016_v2_schema.py` 同步 — 改一处两处都要改。
migration 不 import 这里,避免应用代码污染 migration 历史。
"""

from __future__ import annotations

NOTE_SOURCE_VALUES: tuple[str, ...] = (
    # notes 用:
    "local_md",
    "web_editor",
    # jds 复用同 ENUM:
    "text_paste",
    "image_upload",
)

QUESTION_TYPE_VALUES: tuple[str, ...] = ("open_ended", "definition")

QUIZ_SESSION_STATUS_VALUES: tuple[str, ...] = (
    "in_progress",
    "submitted",
    "abandoned",
)

QUIZ_SESSION_MODE_VALUES: tuple[str, ...] = (
    "topic",  # M2:聊天框主题类 query
    "job",    # M3:岗位类(笔记 + 简历 + JD 子集 三源融合)
    "auto",   # M3:空 query(SR 自选 heading_path 末段填入)
)
