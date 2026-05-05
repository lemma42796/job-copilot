"""jds: add source_url (text) + source_publisher (varchar(50))

P2 schema 改造(B25):JD 元数据缺 raw_url / source_publisher,后续追溯
/ 反爬要手动补。前端粘贴时让用户填(或粘贴侧识别 URL),不进 LLM
抽取(LLM 只看正文,不知道源链接)。

`source_url`:粘贴的 BOSS / LinkedIn / 邮件链接原文(Text 不限长)
`source_publisher`:平台标识('boss' / 'linkedin' / 'liepin' / 'email' /
'manual' 等),前端识别 URL host 后填入,后端不强枚举(M2 只用于展示
+ 后续匹配维度;M5 反爬时再升 ENUM)。

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jds", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("jds", sa.Column("source_publisher", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("jds", "source_publisher")
    op.drop_column("jds", "source_url")
