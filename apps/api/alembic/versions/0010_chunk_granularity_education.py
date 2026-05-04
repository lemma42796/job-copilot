"""chunk_granularity: add 'education' value

S8 chunker 设计期漏了 educations 表 → `build_chunks` 只切 summary /
experience / project / skill 4 类。M2 匹配场景里 JD 常带教育要求
(本科及以上 / 计算机相关专业 / GPA 3.5+),没 education chunk 就召
回不到。S11 dogfood 实证 bad case(profile 13:DB 有 1 条教育,
chunks 表 0 条 education)。

PG enum 不支持 REMOVE VALUE,downgrade 走 noop:0005 的 downgrade 把
整个 chunk_granularity type 一起 drop,本 migration 不需要单独撤销
ADD VALUE。round-trip(head → base → head)仍正确:第二次 upgrade
时 0005 创建 4 值 enum,本 migration 再 ADD VALUE 加回 'education'。

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE chunk_granularity ADD VALUE IF NOT EXISTS 'education'")


def downgrade() -> None:
    # PG enum 不支持 REMOVE VALUE。0005 downgrade 会 drop 整个 type,
    # 这里 noop 不影响 round-trip 正确性(见模块 docstring)。
    pass
