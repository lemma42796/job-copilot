"""M2.1 InterviewCoachAgent state and event log.

DATA_MODEL §5.4-§5.6 落地:

1. quiz_sessions 增加 agent_state / last_agent_node,保存可恢复 checkpoint。
2. session_answers 增加 answer_turns / remediation_state,支持单题多轮补答
   与累计答案重评。
3. 新增 session_events append-only 事件表,用于回放、恢复、context pack
   压缩审计、remediation 证据审计和 Langfuse trace 对齐。

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quiz_sessions",
        sa.Column(
            "agent_state",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "quiz_sessions",
        sa.Column("last_agent_node", sa.String(50)),
    )

    op.add_column(
        "session_answers",
        sa.Column(
            "answer_turns",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "session_answers",
        sa.Column(
            "remediation_state",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("answer_id", sa.BigInteger()),
        sa.Column("question_id", sa.BigInteger()),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("agent_node", sa.String(50)),
        sa.Column(
            "round_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["quiz_sessions.id"],
            ondelete="CASCADE",
            name="fk_se_session_id",
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["session_answers.id"],
            ondelete="CASCADE",
            name="fk_se_answer_id",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name="fk_se_question_id",
        ),
    )
    op.create_index(
        "ix_session_events_session",
        "session_events",
        ["session_id", "id"],
    )
    op.create_index(
        "ix_session_events_answer",
        "session_events",
        ["answer_id", "round_index"],
        postgresql_where=sa.text("answer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_session_events_answer", table_name="session_events")
    op.drop_index("ix_session_events_session", table_name="session_events")
    op.drop_table("session_events")
    op.drop_column("session_answers", "remediation_state")
    op.drop_column("session_answers", "answer_turns")
    op.drop_column("quiz_sessions", "last_agent_node")
    op.drop_column("quiz_sessions", "agent_state")
