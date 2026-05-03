"""jds: add salary_months smallint NULL

JD 写明的"X 薪"数(13/14/15/16 等),代表年终奖月数;JD 未写则 NULL。
JDParser prompt 由 v1.0.0 同步 bump 到 v1.0.1 抽取该字段。

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jds", sa.Column("salary_months", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("jds", "salary_months")
