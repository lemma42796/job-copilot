"""matches 表 + match_status enum

S13: 匹配检索骨架。建表预备 S14 MatchAnalystAgent。

偏离 DATA_MODEL §3.9
--------------------
- score 改 nullable: 永久约束 #4 要求 SSE 起手 emit `started` 时已经有
  resource_id,所以 phase 1 必须 INSERT 一条 pending 行,此时 score 还没
  算出来; analyze 失败也保留行 (status='failed') 留诊断而非 DELETE,
  与 jds.status='parse_failed' 模式对齐。
- 新增 match_status ENUM('pending','scored','failed'): 同上,且承载 SSE
  状态机的可观测性。
- ck_matches_score_range 改成"NULL OR (0..100)",NOT NULL 留给 status='scored'
  阶段由 service 层保证(score 字段直接是 LLM 输出,数据库只兜底范围)。

设计文档(§3.9)将在 S13 归档卡里更新一行说明此偏离;模型本身保持 §3.9
列名/类型不变。

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MATCH_STATUS_VALUES = ("pending", "scored", "failed")


def upgrade() -> None:
    match_status = postgresql.ENUM(
        *MATCH_STATUS_VALUES, name="match_status", create_type=False
    )
    match_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("jd_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="match_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("score", sa.SmallInteger()),
        sa.Column(
            "matched_skills",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_skills",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("advantage_summary", sa.Text()),
        sa.Column("gap_summary", sa.Text()),
        sa.Column(
            "suggestions",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.String(50)),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("cost_cny", sa.Numeric(10, 4)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_matches_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["jd_id"], ["jds.id"], ondelete="CASCADE", name="fk_matches_jd_id"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            ondelete="CASCADE",
            name="fk_matches_profile_id",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_matches_score_range",
        ),
    )

    op.create_index(
        "idx_matches_user_jd",
        "matches",
        ["user_id", "jd_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute(
        """
        CREATE TRIGGER tg_matches_set_updated_at
        BEFORE UPDATE ON matches
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tg_matches_set_updated_at ON matches")
    op.drop_index("idx_matches_user_jd", table_name="matches")
    op.drop_table("matches")
    postgresql.ENUM(name="match_status", create_type=False).drop(
        op.get_bind(), checkfirst=False
    )
