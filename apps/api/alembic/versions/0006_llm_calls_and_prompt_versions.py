"""llm_calls + prompt_versions

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # prompt_versions first: llm_calls has FK -> prompt_versions.id
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("eval_score", sa.Numeric(5, 2)),
        # eval_run_id intentionally has no FK: eval_runs ships in S6.
        sa.Column("eval_run_id", sa.BigInteger()),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("agent_name", "version", name="uq_pv_agent_version"),
    )
    op.create_index(
        "idx_pv_agent_active",
        "prompt_versions",
        ["agent_name", "is_active"],
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("feature", sa.String(50), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column(
            "thinking_mode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column(
            "cached_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_cny", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column("trace_id", sa.String(100)),
        sa.Column("related_entity", sa.String(50)),
        sa.Column("related_id", sa.BigInteger()),
        sa.Column("prompt_version_id", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_lc_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"],
            ["prompt_versions.id"],
            ondelete="SET NULL",
            name="fk_lc_prompt_version_id",
        ),
    )
    op.create_index(
        "idx_lc_user_feature_created",
        "llm_calls",
        ["user_id", "feature", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_lc_created",
        "llm_calls",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_lc_feature_success",
        "llm_calls",
        ["feature", "success"],
    )


def downgrade() -> None:
    op.drop_index("idx_lc_feature_success", table_name="llm_calls")
    op.drop_index("idx_lc_created", table_name="llm_calls")
    op.drop_index("idx_lc_user_feature_created", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index("idx_pv_agent_active", table_name="prompt_versions")
    op.drop_table("prompt_versions")
