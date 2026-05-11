"""M2 schema 调整:聊天框 query 出题入口替代节点点击。

DATA_MODEL §5.3 / §5.4 / §5.9 落地:

1. CREATE TYPE quiz_session_mode('topic','job','auto')
2. quiz_sessions 改造:
   - DROP node_folder_path / node_heading_path(节点点击入口废弃)
   - ADD query / mode / jd_ids(出题入口三件)
   - ADD trigger / gap_folder_path / gap_heading_path(M3 SR 审计字段;
     M2 一并预建,避免 M3 切片再动 quiz 表)
   - ADD expanded_queries / retrieved_chunk_ids(retrieval pipeline 审计快照)
   - ADD INDEX ix_quiz_sessions_mode
3. questions 改造:
   - DROP node_folder_path / node_heading_path
   - ADD originated_query / originated_mode
   - DROP INDEX ix_questions_node_folder
   - ADD INDEX ix_questions_originated_mode
4. resumes 加 uq_resumes_singleton(全库 deleted_at IS NULL 至多 1 行)

空表守门:M1 只到笔记入库,quiz_sessions / questions 在产线必空。
非空 → 直接 RuntimeError 阻止迁移(防止 ALTER TABLE 因 NOT NULL ADD COLUMN
缺 default 静默失败 / 数据丢失)。

downgrade 不实做(沿用 0016 模式,M2 schema 是单向重构)。

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


QUIZ_SESSION_MODE_VALUES = ("topic", "job", "auto")


def upgrade() -> None:
    bind = op.get_bind()

    # 0. 空表守门 — M1 收口阶段 quiz_sessions / questions 必空,
    # 非空说明 dogfood 已经跑过 M2,本迁移不应再被执行
    for table in ("quiz_sessions", "questions"):
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
        if count and count > 0:
            raise RuntimeError(
                f"{table} 有 {count} 行,M2 schema 迁移假设空表;"
                f"如确需保留旧数据请先手工备份 + 重写本 migration"
            )

    # 1. CREATE TYPE quiz_session_mode
    quiz_session_mode = postgresql.ENUM(
        *QUIZ_SESSION_MODE_VALUES,
        name="quiz_session_mode",
        create_type=False,
    )
    quiz_session_mode.create(bind, checkfirst=False)

    # 2. quiz_sessions
    op.drop_column("quiz_sessions", "node_folder_path")
    op.drop_column("quiz_sessions", "node_heading_path")

    op.add_column(
        "quiz_sessions",
        sa.Column("query", sa.Text(), nullable=False),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column(
            "mode",
            postgresql.ENUM(name="quiz_session_mode", create_type=False),
            nullable=False,
            server_default="topic",
        ),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("jd_ids", postgresql.ARRAY(sa.BigInteger())),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column(
            "trigger",
            sa.String(20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("gap_folder_path", postgresql.ARRAY(sa.Text())),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("gap_heading_path", postgresql.ARRAY(sa.Text())),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("expanded_queries", postgresql.ARRAY(sa.Text())),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("retrieved_chunk_ids", postgresql.ARRAY(sa.BigInteger())),
    )
    op.create_index(
        "ix_quiz_sessions_mode",
        "quiz_sessions",
        ["mode"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 3. questions
    op.drop_index("ix_questions_node_folder", table_name="questions")
    op.drop_column("questions", "node_folder_path")
    op.drop_column("questions", "node_heading_path")

    op.add_column(
        "questions",
        sa.Column("originated_query", sa.Text(), nullable=False),
    )
    op.add_column(
        "questions",
        sa.Column(
            "originated_mode",
            postgresql.ENUM(name="quiz_session_mode", create_type=False),
            nullable=False,
            server_default="topic",
        ),
    )
    op.create_index(
        "ix_questions_originated_mode",
        "questions",
        ["originated_mode"],
    )

    # 4. resumes singleton 唯一约束(全库 deleted_at IS NULL 至多 1 行)
    op.execute(
        "CREATE UNIQUE INDEX uq_resumes_singleton "
        "ON resumes ((true)) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "M2 schema 迁移不支持 downgrade(出题入口语义不兼容);"
        "需要回退请走 git checkout v0.3-m1-end 重建数据库。"
    )
